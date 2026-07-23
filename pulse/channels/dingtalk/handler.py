from __future__ import annotations

import logging
from pathlib import Path

import dingtalk_stream
from dingtalk_stream import AckMessage

from pulse.channels.dingtalk.files import (
    extract_file_attachment,
    extract_incoming_text,
    extract_picture_download_code,
    inbox_dest,
    incoming_message_type,
)
from pulse.channels.commands import CURSOR_BIND_GUIDE
from pulse.channels.dingtalk.guide_image import (
    save_guide_image_override,
)
from pulse.channels.dingtalk.work_group import (
    activate_work_group,
    is_work_group_activation,
    persist_work_group_binding,
    sync_group_display_name,
)
from pulse.channels.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig
from pulse.extract.text_parser import looks_like_usage_csv
from pulse.extract.summary import format_group_ack
from pulse.llm.client import build_llm_client
from pulse.llm.vision import extract_vendor_usage_from_screenshot
from pulse.tool_center.manual import (
    ManualUsageCommand,
    ManualUsageService,
    infer_vendor_slug_from_text,
    pick_account_for_screenshot,
    pick_account_for_vendor,
    vendor_display_name,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.periods import current_period
from pulse.tenant.context import team_repository

logger = logging.getLogger(__name__)


class DingTalkChannelHandler(dingtalk_stream.ChatbotHandler):
    def __init__(
        self,
        config: AppConfig,
        session_factory,
        messenger: DingTalkMessenger,
        logger: logging.Logger | None = None,
    ):
        super().__init__()
        self.pulse_config = config
        self.session_factory = session_factory
        self.messenger = messenger
        self._pending_guide_upload: set[str] = set()
        if logger:
            self.logger = logger

    def reply_text(self, text: str, incoming_message: dingtalk_stream.ChatbotMessage):
        from pulse.channels.dingtalk.messenger import _looks_like_markdown_message

        if _looks_like_markdown_message(text) and incoming_message.session_webhook:
            try:
                self.messenger.reply_session_text(
                    incoming_message.session_webhook,
                    text,
                    at_user_id=incoming_message.sender_staff_id,
                )
                return None
            except Exception:
                logger.exception("markdown session reply failed; falling back to plain text")
        return super().reply_text(text, incoming_message)

    def _is_admin(self, user_id: str) -> bool:
        from pulse.channels.admin_gate import is_dingtalk_admin

        return is_dingtalk_admin(user_id, self.pulse_config.admin.dingtalk_user_ids)

    def _send_user_detail(
        self,
        *,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
        detail: str,
    ) -> None:
        if channel == "group":
            self.reply_text(format_group_ack(user_name), incoming)
            self.messenger.send_oto_text(user_id, detail)
        else:
            self.reply_text(detail, incoming)

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        try:
            await self._handle_message(incoming, callback.data)
            return AckMessage.STATUS_OK, "OK"
        except Exception:
            logger.exception("Failed to handle message")
            self.reply_text("处理失败，请稍后重试或私聊联系管理员。", incoming)
            return AckMessage.STATUS_OK, "ERROR"

    async def _handle_message(self, incoming: dingtalk_stream.ChatbotMessage, raw: dict) -> None:
        """Channel adapter：文本一律镜像给 Assistant；本地仅处理文件/图/引导图状态机。"""
        is_group = incoming.conversation_type == "2"

        if is_group and not incoming.is_in_at_list:
            return

        user_id = incoming.sender_staff_id or incoming.sender_id
        user_name = incoming.sender_nick or user_id

        text = extract_incoming_text(incoming)
        picture_code = extract_picture_download_code(raw, incoming)
        if not picture_code and incoming.message_type == "picture" and incoming.image_content:
            picture_code = incoming.image_content.download_code
        handle_picture_locally = bool(picture_code)

        if is_group and text and is_work_group_activation(text):
            session = self.session_factory()
            try:
                team, repo = team_repository(session, self.pulse_config)
                member = repo.get_or_create_member(user_id, user_name)
                actor_role = member.portal_role
                if actor_role not in ("owner", "operator") and self._is_admin(user_id):
                    actor_role = "owner"
                result = activate_work_group(
                    self.pulse_config,
                    session,
                    team_id=team.id,
                    incoming=incoming,
                    user_id=user_id,
                    member_id=member.id,
                    member_portal_role=actor_role,
                )
                session.commit()
                if result.reply:
                    self.reply_text(result.reply, incoming)
                if result.handled:
                    return
            except Exception:
                session.rollback()
                logger.exception("Work group activation failed")
                self.reply_text("工作群激活失败，请稍后重试或联系管理员。", incoming)
                return
            finally:
                session.close()

        if is_group and incoming.conversation_id:
            self._ensure_group_binding(incoming)

        if not handle_picture_locally:
            try:
                from pulse.channels.dingtalk.mirror import mirror_dingtalk_message

                session = self.session_factory()
                try:
                    team, repo = team_repository(session, self.pulse_config)
                    member = repo.get_or_create_member(user_id, user_name)
                    session.commit()
                    actor_role = member.portal_role
                    if actor_role not in ("owner", "operator") and self._is_admin(user_id):
                        actor_role = "owner"
                    mirror_dingtalk_message(
                        incoming,
                        text=text or "",
                        config=self.pulse_config,
                        team_id=team.id,
                        is_group=is_group,
                        actor_member_id=member.id,
                        actor_role=actor_role,
                    )
                    if is_group and incoming.conversation_title:
                        sync_group_display_name(
                            self.pulse_config,
                            session,
                            team_id=team.id,
                            title=incoming.conversation_title,
                            member_id=member.id,
                        )
                        session.commit()
                finally:
                    session.close()
            except Exception:
                logger.exception("Assistant mirror hook crashed; continuing")

        # 引导图状态机：需本地记住「下一条发图」
        if text == "设置引导图":
            await self._begin_guide_image_upload(incoming, user_id, user_name)
            return

        # 粘贴 CSV 仍走本地（非文本命令）
        if text and looks_like_usage_csv(text):
            channel = "group" if is_group else "private"
            await self._handle_pasted_csv(text, incoming, user_id, user_name, channel)
            return

        if handle_picture_locally and picture_code:
            if user_id in self._pending_guide_upload:
                await self._save_guide_image_from_picture(
                    picture_code, incoming, user_id, is_group
                )
                return
            await self._handle_picture(
                picture_code,
                incoming,
                user_id,
                user_name,
                is_group,
                text_hint=text,
            )
            return

        # 其余文本：已镜像，由 Assistant 经 channel/reply 回复
        if text:
            return

        file_path, unsupported_name = self._download_attachment(incoming, raw)
        if unsupported_name:
            self.reply_text(
                f"收到文件「{unsupported_name}」，但仅支持非 Cursor 厂商的用量 CSV/Excel（.csv / .xlsx）。\n\n"
                f"{CURSOR_BIND_GUIDE}",
                incoming,
            )
            return
        if file_path:
            channel = "group" if is_group else "private"
            await self._handle_csv_file(file_path, incoming, user_id, user_name, channel)
            return

        if incoming_message_type(raw, incoming) == "file":
            logger.warning(
                "Unhandled file message: msg_id=%s keys=%s extensions=%s",
                incoming.message_id,
                sorted(raw.keys()),
                sorted(getattr(incoming, "extensions", {}).keys()),
            )
            self.reply_text(
                "收到文件但无法识别或下载。Cursor 请绑定 API Key；其他工具请发送用量 CSV/Excel。",
                incoming,
            )

    async def _begin_guide_image_upload(
        self,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
    ) -> None:
        from pulse.authz.actor import can_manage_guide_image

        session = self.session_factory()
        try:
            _team, repo = team_repository(session, self.pulse_config)
            member = repo.get_or_create_member(user_id, user_name)
            allowed = can_manage_guide_image(member, self.pulse_config)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Guide image authz check failed")
            self.reply_text("命令执行失败：权限校验异常", incoming)
            return
        finally:
            session.close()

        if not allowed:
            self.reply_text("无权限。", incoming)
            return
        self._pending_guide_upload.add(user_id)
        self.reply_text(
            "请在下一条消息发送 Cursor 绑 Key 引导截图（图片消息）。",
            incoming,
        )
    async def _handle_csv_file(
        self,
        file_path: Path,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
    ) -> None:
        self._send_user_detail(
            incoming=incoming,
            user_id=user_id,
            user_name=user_name,
            channel=channel,
            detail=(
                "Cursor 已改用 API Key 自动同步，不再接受 CSV 上传。\n\n"
                f"{CURSOR_BIND_GUIDE}"
            ),
        )

    async def _handle_pasted_csv(
        self,
        text: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
    ) -> None:
        self._send_user_detail(
            incoming=incoming,
            user_id=user_id,
            user_name=user_name,
            channel=channel,
            detail=(
                "Cursor 已改用 API Key 自动同步，不再接受 CSV 粘贴。\n\n"
                f"{CURSOR_BIND_GUIDE}"
            ),
        )

    async def _handle_picture(
        self,
        download_code: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        is_group: bool,
        *,
        text_hint: str = "",
    ) -> None:
        raw_dir = Path(self.pulse_config.storage.raw_files_dir)
        dest = inbox_dest(raw_dir, "usage_screenshot.png")
        try:
            self.messenger.download_message_file(download_code, dest)
        except Exception as exc:
            logger.exception("Picture download failed")
            msg = f"截图下载失败：{exc}"
            if is_group:
                self.reply_text(f"@{user_name} 处理失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, msg)
            else:
                self.reply_text(msg, incoming)
            return

        channel = "group" if is_group else "private"
        session = self.session_factory()
        primary_accounts: list = []
        hinted_vendor = None
        runtime_config = self.pulse_config
        try:
            team, repo = team_repository(session, self.pulse_config)
            from pulse.settings import effective_config

            runtime_config = effective_config(self.pulse_config, session, team.id)
            member = repo.get_or_create_member(user_id, user_name)
            tool_repo = ToolCenterRepository(session, team.id)
            primary_accounts = tool_repo.get_primary_accounts_for_member(member.id)
            hinted_vendor = infer_vendor_slug_from_text(text_hint)
            screenshot_account = None
            vendor_slug = "cursor"

            if hinted_vendor:
                vendor_slug = hinted_vendor
                try:
                    screenshot_account = pick_account_for_vendor(primary_accounts, hinted_vendor)
                except ValueError:
                    screenshot_account = None
            else:
                screenshot_account = pick_account_for_screenshot(primary_accounts)
                if screenshot_account and screenshot_account.vendor:
                    vendor_slug = screenshot_account.vendor.slug
        finally:
            session.close()

        llm = runtime_config.llm
        client = build_llm_client(runtime_config)

        if hinted_vendor and not screenshot_account:
            vendor_name = vendor_display_name(hinted_vendor)
            self._send_user_detail(
                incoming=incoming,
                user_id=user_id,
                user_name=user_name,
                channel=channel,
                detail=(
                    f"📷 截图已收到，但未找到您名下的{vendor_name}账号。\n\n"
                    f"台账里已有 {vendor_name} 账号时，请确认：\n"
                    "· 该账号「主使用人」是您本人\n"
                    "· 您用绑定了主使用人的同一钉钉账号私聊机器人\n\n"
                    f"也可改用手工上报，例如：上报 {vendor_name} 85"
                ),
            )
            return

        vendor_vision_slugs = ("zhipu", "minimax", "codex")
        if vendor_slug in vendor_vision_slugs:
            if not llm.vision_enabled or not client:
                vendor_name = vendor_display_name(vendor_slug)
                self._send_user_detail(
                    incoming=incoming,
                    user_id=user_id,
                    user_name=user_name,
                    channel=channel,
                    detail=(
                        f"📷 已识别为{vendor_name}截图，但视觉识别未就绪。\n\n"
                        "请在管理后台启用 Pulse LLM 视觉解析并配置 API Key，"
                        f"或改用手工上报：上报 {vendor_name} <数值>"
                    ),
                )
                return
            try:
                await self._submit_vendor_screenshot(
                    dest,
                    incoming,
                    user_id,
                    user_name,
                    channel,
                    is_group,
                    vendor_slug=vendor_slug,
                    client=client,
                    model=llm.vision_model,
                    threshold=llm.confidence_threshold,
                    screenshot_account=screenshot_account,
                )
                return
            except Exception as exc:
                logger.exception("Vendor vision extraction failed")
                vendor_name = vendor_display_name(vendor_slug)
                self._send_user_detail(
                    incoming=incoming,
                    user_id=user_id,
                    user_name=user_name,
                    channel=channel,
                    detail=(
                        f"📷 {vendor_name} 截图识别失败：{exc}\n\n"
                        f"请稍后重试，或改用手工上报：上报 {vendor_name} <数值>"
                    ),
                )
                return

        if primary_accounts and len(primary_accounts) > 1 and not screenshot_account:
            vendors = sorted({a.vendor.name for a in primary_accounts if a.vendor})
            reply = (
                "📷 截图已收到。您有多个工具账号，无法自动判断工具类型。\n\n"
                f"请使用手工上报：上报 <工具> <数值>\n"
                f"可选工具：{'、'.join(vendors)}"
            )
            if is_group:
                self.reply_text(format_group_ack(user_name), incoming)
                self.messenger.send_oto_text(user_id, reply)
            else:
                self.reply_text(reply, incoming)
            return

        self._send_user_detail(
            incoming=incoming,
            user_id=user_id,
            user_name=user_name,
            channel=channel,
            detail=(
                "📷 截图已收到。Cursor 用量请绑定 API Key 自动同步；"
                "其他工具请发送对应厂商截图或手工上报。\n\n"
                f"{CURSOR_BIND_GUIDE}"
            ),
        )

    async def _submit_vendor_screenshot(
        self,
        dest: Path,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
        is_group: bool,
        *,
        vendor_slug: str,
        client,
        model: str,
        threshold: float,
        screenshot_account,
    ) -> None:
        result = extract_vendor_usage_from_screenshot(
            dest, client, vendor_slug=vendor_slug, model=model
        )
        if result.confidence < threshold:
            warn_lines = result.warnings or ["截图内容不完整或置信度不足"]
            reply = (
                f"📷 {vendor_slug} 截图识别置信度不足（{result.confidence:.0%}）。\n\n"
                + "\n".join(f"· {w}" for w in warn_lines)
                + f"\n\n请改用手工上报，例如：上报 {vendor_slug} 85"
            )
            if is_group:
                self.reply_text(format_group_ack(user_name), incoming)
                self.messenger.send_oto_text(user_id, reply)
            else:
                self.reply_text(reply, incoming)
            return

        session = self.session_factory()
        team, repo = team_repository(session, self.pulse_config)
        try:
            member = repo.get_or_create_member(user_id, user_name)
            period = current_period(self.pulse_config)
            if result.period_hint and len(result.period_hint) == 7:
                period = result.period_hint

            command = ManualUsageCommand(
                vendor_slug=vendor_slug,
                metric_value=result.primary_metric_value,
                metric_unit=result.primary_metric_unit,
                raw_text=f"screenshot:{vendor_slug}",
            )
            svc = ManualUsageService(session, team.id)
            account_id = screenshot_account.id if screenshot_account else None
            _, account, summary = svc.submit_for_member(
                member=member,
                period=period,
                command=command,
                submit_channel=channel,
                account_id=account_id,
                repo=repo,
                raw_source=dest,
                raw_files_dir=Path(self.pulse_config.storage.raw_files_dir),
                extraction_confidence=result.confidence,
                breakdown_by_model=result.breakdown_by_model or None,
                source_type="manual_vision",
                upgrade_notify=(
                    self.messenger.send_oto_text,
                    list(self.pulse_config.admin.dingtalk_user_ids),
                )
                if self.pulse_config.admin.dingtalk_user_ids
                else None,
            )
            repo.commit()

            vendor_name = account.vendor.name if account.vendor else vendor_slug
            ratio = summary.get("quota_usage_ratio")
            ratio_line = f"\n额度使用率：{ratio}%" if ratio is not None else ""
            notes = ""
            if result.warnings:
                notes = "\n\n" + "\n".join(result.warnings)
            detail = (
                f"✅ {period} {vendor_name} 截图用量已入库\n"
                f"账号：{account.account_identifier}\n"
                f"主指标：{summary['primary_metric_value']} {summary['primary_metric_unit'].upper()}"
                f"{ratio_line}"
                f"\n识别置信度：{result.confidence:.0%}"
                f"\n已计入统计。"
                f"{notes}"
            )
            if channel == "group":
                self.reply_text(format_group_ack(user_name), incoming)
                self.messenger.send_oto_text(user_id, detail)
            else:
                self.reply_text(detail, incoming)
        except Exception as exc:
            session.rollback()
            logger.exception("Vendor screenshot ingestion failed")
            msg = f"截图用量保存失败：{exc}"
            if is_group:
                self.reply_text(f"@{user_name} 处理失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, msg)
            else:
                self.reply_text(msg, incoming)
        finally:
            session.close()

    async def _save_guide_image_from_picture(
        self,
        download_code: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        is_group: bool,
    ) -> None:
        self._pending_guide_upload.discard(user_id)
        dest = inbox_dest(Path(self.pulse_config.storage.raw_files_dir), "cursor_bind_key_guide.png")
        reply = "✅ 绑 Key 引导图已更新，后续借 Key 提示将自动配图。"
        try:
            self.messenger.download_message_file(download_code, dest)
            if self.pulse_config.capability_bridge.guide_image_update:
                try:
                    import base64

                    from pulse.channels.capability_bridge import invoke_via_assistant

                    session = self.session_factory()
                    try:
                        _team, repo = team_repository(session, self.pulse_config)
                        member = repo.get_member_by_dingtalk_id(user_id)
                        if member is None:
                            raise ValueError("未找到成员记录")
                        image_b64 = base64.b64encode(dest.read_bytes()).decode("ascii")
                        bridge_reply = invoke_via_assistant(
                            config=self.pulse_config,
                            team_id=repo.team_id,
                            member_id=member.id,
                            role=member.portal_role,
                            capability_key="guide_image.update",
                            arguments={"image_base64": image_b64},
                            confirmed=True,
                        )
                        session.commit()
                        self.messenger.clear_image_media_cache()
                        reply = (
                            bridge_reply
                            if bridge_reply.startswith("✅")
                            else f"✅ {bridge_reply}"
                        )
                    finally:
                        session.close()
                except Exception:
                    logger.exception(
                        "Capability bridge failed for guide_image.update; falling back to legacy save"
                    )
                    save_guide_image_override(self.pulse_config.storage.raw_files_dir, dest)
                    self.messenger.clear_image_media_cache()
            else:
                save_guide_image_override(self.pulse_config.storage.raw_files_dir, dest)
                self.messenger.clear_image_media_cache()
        except Exception as exc:
            logger.exception("Guide image save failed")
            reply = f"引导图保存失败：{exc}"
        if is_group:
            self.reply_text("引导图已处理，详情见私聊。", incoming)
            self.messenger.send_oto_text(user_id, reply)
        else:
            self.reply_text(reply, incoming)

    def _download_attachment(
        self,
        incoming: dingtalk_stream.ChatbotMessage,
        raw: dict,
    ) -> tuple[Path | None, str | None]:
        attachment = extract_file_attachment(raw, incoming)
        if attachment:
            file_name, download_code = attachment
            suffix = Path(file_name).suffix.lower()
            if suffix not in (".csv", ".xlsx", ".xls"):
                logger.warning("Ignored unsupported usage file: %s", file_name)
                return None, file_name
            dest = inbox_dest(Path(self.pulse_config.storage.raw_files_dir), file_name)
            return self.messenger.download_message_file(download_code, dest), None

        # 图片已在上方单独处理
        return None, None

    def _ensure_group_binding(self, incoming: dingtalk_stream.ChatbotMessage) -> None:
        """首次群消息时自动保存 openConversationId（Stream 回调里的 conversationId）。"""
        if self.pulse_config.dingtalk.group_open_conversation_id:
            return
        open_id = incoming.conversation_id
        if not open_id:
            return
        title = incoming.conversation_title or None
        session = self.session_factory()
        try:
            team, _repo = team_repository(session, self.pulse_config)
            persist_work_group_binding(
                self.pulse_config,
                session,
                team_id=team.id,
                open_conversation_id=open_id,
                chat_id=self.pulse_config.dingtalk.chat_id or None,
                title=title,
                member_id=None,
            )
            session.commit()
            logger.info(
                "已自动绑定群 openConversationId=%s title=%s chat_id=%s",
                open_id,
                title,
                self.pulse_config.dingtalk.chat_id,
            )
        except Exception:
            session.rollback()
            logger.exception("Auto group binding failed")
        finally:
            session.close()
