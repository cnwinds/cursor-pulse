from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import dingtalk_stream
from dingtalk_stream import AckMessage

from pulse.bot.dingtalk.files import (
    extract_file_attachment,
    extract_picture_download_code,
    inbox_dest,
    incoming_message_type,
    normalize_incoming_text,
)
from pulse.bot.pending_submission import PendingIngestionStore, PendingUsageIngestion
from pulse.bot.dingtalk.group_store import save_group_binding
from pulse.bot.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig
from pulse.extract.csv_parser import parse_usage_events_csv, parse_usage_events_file
from pulse.extract.text_parser import looks_like_usage_csv
from pulse.domain import ParsedCsv
from pulse.extract.period_split import split_parsed_by_period
from pulse.extract.summary import (
    format_auto_split_notice,
    format_extraction_confidence_note,
    format_group_ack,
    format_group_submit_private_footer,
    format_pool_spend_note,
    format_split_period_confirmation,
)
from pulse.llm.client import build_llm_client
from pulse.llm.vision import extract_usage_from_screenshot, extract_vendor_usage_from_screenshot
from pulse.tool_center.manual import (
    ManualUsageCommand,
    ManualUsageService,
    looks_like_manual_usage,
    pick_account_for_screenshot,
)
from pulse.tool_center.account_pick import (
    can_proxy_submit_for_others,
    filter_cursor_accounts,
    find_cursor_account_in_pool,
    format_cursor_account_choice_prompt,
    looks_like_account_selection_cancel,
    parse_account_selection_text,
    parse_proxy_member_name,
    resolve_member_by_display_name,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.periods import current_period
from pulse.tool_center.knowledge import looks_like_tip
from pulse.storage.models import Member, UsageSummary
from pulse.storage.repository import Repository, input_type_from_source_type, source_type_from_input_type
from pulse.tenant.context import team_repository
from sqlalchemy import select

logger = logging.getLogger(__name__)


class PulseBotHandler(dingtalk_stream.ChatbotHandler):
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
        if logger:
            self.logger = logger
        pending_path = Path(config.storage.raw_files_dir) / "pending_ingestions.json"
        self._pending_store = PendingIngestionStore(pending_path)

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

    async def _try_complete_pending_account_selection(
        self,
        text: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        is_group: bool,
    ) -> bool:
        pending = self._pending_store.get(user_id)
        if not pending:
            return False

        if looks_like_account_selection_cancel(text):
            self._pending_store.clear(user_id)
            reply = "已取消本次用量提交。需要时请重新发送文件。"
            if is_group:
                self.reply_text(format_group_ack(user_name), incoming)
                self.messenger.send_oto_text(user_id, reply)
            else:
                self.reply_text(reply, incoming)
            return True

        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.pulse_config)
            tool_repo = ToolCenterRepository(session, team.id)
            submitter = repo.get_or_create_member(user_id, user_name)
            is_proxy_admin = can_proxy_submit_for_others(self.pulse_config, submitter)
            candidates = [
                account
                for account_id in pending.account_ids
                if (account := tool_repo.get_account(account_id))
            ]
            selected = parse_account_selection_text(text, candidates)
            proxy_target_name: str | None = None

            if not selected and is_proxy_admin:
                selected = find_cursor_account_in_pool(text, tool_repo.list_active_accounts())
                if not selected:
                    proxy_name = parse_proxy_member_name(text)
                    if proxy_name:
                        target = resolve_member_by_display_name(
                            repo.list_active_members(),
                            proxy_name,
                        )
                        if not target:
                            reply = (
                                f"未找到成员「{proxy_name}」，请核对姓名后重试。\n\n"
                                f"{format_cursor_account_choice_prompt(candidates, admin_hint=True)}"
                            )
                            if is_group:
                                self.reply_text(format_group_ack(user_name), incoming)
                                self.messenger.send_oto_text(user_id, reply)
                            else:
                                self.reply_text(reply, incoming)
                            return True

                        target_accounts = filter_cursor_accounts(
                            tool_repo.get_primary_accounts_for_member(target.id)
                        )
                        if not target_accounts:
                            reply = (
                                f"「{target.display_name}」尚未配置 Cursor 主使用人账号，"
                                "请先在台账中添加。\n\n"
                                f"{format_cursor_account_choice_prompt(candidates, admin_hint=True)}"
                            )
                            if is_group:
                                self.reply_text(format_group_ack(user_name), incoming)
                                self.messenger.send_oto_text(user_id, reply)
                            else:
                                self.reply_text(reply, incoming)
                            return True

                        if len(target_accounts) == 1:
                            selected = target_accounts[0]
                            proxy_target_name = target.display_name
                        else:
                            self._pending_store.save(
                                PendingUsageIngestion(
                                    dingtalk_user_id=pending.dingtalk_user_id,
                                    user_name=pending.user_name,
                                    channel=pending.channel,
                                    source_type=pending.source_type,
                                    account_ids=[a.id for a in target_accounts],
                                    created_at=pending.created_at,
                                    file_path=pending.file_path,
                                    raw_text=pending.raw_text,
                                    extraction_confidence=pending.extraction_confidence,
                                    status=pending.status,
                                    extra_notes=pending.extra_notes,
                                    notify_admins_review=pending.notify_admins_review,
                                )
                            )
                            prompt = (
                                f"管理员代提交：将为 {target.display_name} 记录用量。\n\n"
                                + format_cursor_account_choice_prompt(
                                    target_accounts,
                                    subject_name=target.display_name,
                                )
                            )
                            if is_group:
                                self.reply_text(format_group_ack(user_name), incoming)
                                self.messenger.send_oto_text(user_id, prompt)
                            else:
                                self.reply_text(prompt, incoming)
                            return True

            if not selected:
                prompt = format_cursor_account_choice_prompt(
                    candidates,
                    admin_hint=is_proxy_admin,
                )
                reply = f"无法识别账号选择，请重新回复。\n\n{prompt}"
                if is_group:
                    self.reply_text(format_group_ack(user_name), incoming)
                    self.messenger.send_oto_text(user_id, reply)
                else:
                    self.reply_text(reply, incoming)
                return True

            allow_proxy = bool(
                selected.primary_member_id
                and selected.primary_member_id != submitter.id
            )
            if allow_proxy and not is_proxy_admin:
                reply = "仅账号主使用人可提交用量，如需代提交请联系管理员。"
                if is_group:
                    self.reply_text(format_group_ack(user_name), incoming)
                    self.messenger.send_oto_text(user_id, reply)
                else:
                    self.reply_text(reply, incoming)
                return True

            if allow_proxy and not proxy_target_name and selected.primary_member_id:
                primary = session.get(Member, selected.primary_member_id)
                if primary:
                    proxy_target_name = primary.display_name

            if pending.file_path:
                parsed = parse_usage_events_file(Path(pending.file_path))
                raw_source = Path(pending.file_path)
                raw_text = None
            elif pending.raw_text:
                parsed = parse_usage_events_csv(pending.raw_text)
                raw_source = None
                raw_text = pending.raw_text
            else:
                self._pending_store.clear(user_id)
                self.reply_text("待提交数据已失效，请重新发送文件。", incoming)
                return True

            success = await self._submit_parsed(
                parsed,
                incoming,
                user_id,
                user_name,
                pending.channel,
                input_type=input_type_from_source_type(pending.source_type),
                raw_source=raw_source,
                raw_text=raw_text,
                extraction_confidence=pending.extraction_confidence,
                extra_notes=pending.extra_notes,
                status=pending.status,
                notify_admins_review=pending.notify_admins_review,
                account_id=selected.id,
                allow_proxy=allow_proxy,
                proxy_target_name=proxy_target_name,
            )
            if success:
                self._pending_store.clear(user_id)
            return True
        except Exception as exc:
            logger.exception("Pending account selection failed")
            reply = f"用量保存失败：{exc}"
            if is_group:
                self.reply_text(f"@{user_name} 处理失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, reply)
            else:
                self.reply_text(reply, incoming)
            return True
        finally:
            session.close()

    async def _begin_cursor_usage_ingestion(
        self,
        parsed: ParsedCsv,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
        *,
        input_type: str,
        raw_source: Path | None = None,
        raw_text: str | None = None,
        extraction_confidence: float = 1.0,
        extra_notes: list[str] | None = None,
        status: str = "confirmed",
        notify_admins_review: bool = False,
    ) -> None:
        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.pulse_config)
            member = repo.get_or_create_member(user_id, user_name)
            tool_repo = ToolCenterRepository(session, team.id)
            cursor_accounts = filter_cursor_accounts(
                tool_repo.get_primary_accounts_for_member(member.id)
            )
            if not cursor_accounts:
                msg = "未找到 Cursor 主使用人账号，请联系管理员在台账中配置后再提交。"
                self._send_user_detail(
                    incoming=incoming,
                    user_id=user_id,
                    user_name=user_name,
                    channel=channel,
                    detail=msg,
                )
                return

            if len(cursor_accounts) == 1:
                await self._submit_parsed(
                    parsed,
                    incoming,
                    user_id,
                    user_name,
                    channel,
                    input_type=input_type,
                    raw_source=raw_source,
                    raw_text=raw_text,
                    extraction_confidence=extraction_confidence,
                    extra_notes=extra_notes,
                    status=status,
                    notify_admins_review=notify_admins_review,
                    account_id=cursor_accounts[0].id,
                )
                return

            self._pending_store.save(
                PendingUsageIngestion(
                    dingtalk_user_id=user_id,
                    user_name=user_name,
                    channel=channel,
                    source_type=source_type_from_input_type(input_type),
                    account_ids=[a.id for a in cursor_accounts],
                    created_at=datetime.now(timezone.utc).isoformat(),
                    file_path=str(raw_source) if raw_source else None,
                    raw_text=raw_text,
                    extraction_confidence=extraction_confidence,
                    status=status,
                    extra_notes=extra_notes,
                    notify_admins_review=notify_admins_review,
                )
            )
            prompt = format_cursor_account_choice_prompt(
                cursor_accounts,
                admin_hint=can_proxy_submit_for_others(self.pulse_config, member),
            )
            self._send_user_detail(
                incoming=incoming,
                user_id=user_id,
                user_name=user_name,
                channel=channel,
                detail=prompt,
            )
        finally:
            session.close()

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
        is_group = incoming.conversation_type == "2"

        if is_group and not incoming.is_in_at_list:
            return

        if is_group and incoming.conversation_id:
            self._ensure_group_binding(incoming)

        user_id = incoming.sender_staff_id or incoming.sender_id
        user_name = incoming.sender_nick or user_id

        text = ""
        if incoming.text and incoming.text.content:
            text = normalize_incoming_text(incoming.text.content)

        if text and looks_like_tip(text):
            await self._handle_tip(text, incoming, user_id, user_name, is_group)
            return

        if text and await self._try_complete_pending_account_selection(
            text, incoming, user_id, user_name, is_group
        ):
            return

        if text and (
            text.startswith("/")
            or text in ("状态", "我的", "帮助", "help")
            or text.startswith("聚合")
            or text.startswith("报告")
            or text.startswith("成员")
            or text.startswith("查询")
            or text.startswith("问 ")
            or text.startswith("导出")
            or text.startswith("告警")
            or text.startswith("待审")
            or text.startswith("确认 ")
            or text.startswith("拒绝 ")
            or text.startswith("申请")
            or text.startswith("审批 ")
            or looks_like_manual_usage(text)
            or looks_like_query(text)
        ):
            await self._handle_command(text, incoming, user_id)
            return

        file_path, unsupported_name = self._download_attachment(incoming, raw)
        if unsupported_name:
            self.reply_text(
                f"收到文件「{unsupported_name}」，但仅支持 Cursor Usage 导出的 CSV 或 Excel（.csv / .xlsx）。\n\n"
                "请在 Cursor Dashboard → Usage → Export 后发送文件。",
                incoming,
            )
            return
        if file_path:
            channel = "group" if is_group else "private"
            await self._handle_csv_file(file_path, incoming, user_id, user_name, channel)
            return

        picture_code = extract_picture_download_code(raw)
        if picture_code or (incoming.message_type == "picture" and incoming.image_content):
            code = picture_code or incoming.image_content.download_code
            if code:
                await self._handle_picture(code, incoming, user_id, user_name, is_group)
                return

        if text and looks_like_usage_csv(text):
            channel = "group" if is_group else "private"
            await self._handle_pasted_csv(text, incoming, user_id, user_name, channel)
            return

        if text:
            await self._handle_conversational_text(text, incoming, user_id, is_group)
            return

        if incoming_message_type(raw, incoming) == "file":
            logger.warning(
                "Unhandled file message: msg_id=%s keys=%s extensions=%s",
                incoming.message_id,
                sorted(raw.keys()),
                sorted(getattr(incoming, "extensions", {}).keys()),
            )
            self.reply_text(
                "收到文件但无法识别或下载，请重新私聊发送 Cursor Usage 导出的 .csv / .xlsx 文件。",
                incoming,
            )

    def _chat_service(self) -> "ChatService":
        from pulse.chat.service import ChatService

        return ChatService(
            self.pulse_config,
            session_factory=self.session_factory,
            messenger=self.messenger,
        )

    async def _handle_conversational_text(
        self,
        text: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        is_group: bool,
    ) -> None:
        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.pulse_config)
            member = repo.get_or_create_member(user_id, incoming.sender_nick or user_id)
            result = self._chat_service().chat(
                session=session,
                team=team,
                repo=repo,
                member=member,
                message=text,
                channel="dingtalk",
                is_group=is_group,
                display_name=member.display_name,
            )
            session.commit()
            self.reply_text(result.reply, incoming)
        except Exception:
            logger.exception("Memory conversational handling failed")
            session.rollback()
            self.reply_text(
                "请发送 Cursor Usage 导出的 CSV 文件，或直接粘贴 CSV 内容。",
                incoming,
            )
        finally:
            session.close()

    async def _handle_csv_file(
        self,
        file_path: Path,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
    ) -> None:
        try:
            parsed = parse_usage_events_file(file_path)
        except Exception as exc:
            if channel == "group":
                self.reply_text(f"@{user_name} 解析失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, f"用量文件解析失败：{exc}")
            else:
                self.reply_text(f"用量文件解析失败：{exc}", incoming)
            return
        await self._begin_cursor_usage_ingestion(
            parsed,
            incoming,
            user_id,
            user_name,
            channel,
            input_type="csv",
            raw_source=file_path,
        )

    async def _handle_pasted_csv(
        self,
        text: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
    ) -> None:
        try:
            parsed = parse_usage_events_csv(text)
        except Exception as exc:
            if channel == "group":
                self.reply_text(f"@{user_name} 解析失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, f"粘贴内容解析失败：{exc}")
            else:
                self.reply_text(f"解析失败：{exc}", incoming)
            return
        await self._begin_cursor_usage_ingestion(
            parsed,
            incoming,
            user_id,
            user_name,
            channel,
            input_type="text",
            raw_text=text,
        )

    async def _handle_picture(
        self,
        download_code: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        is_group: bool,
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
        llm = self.pulse_config.llm
        client = build_llm_client(self.pulse_config)

        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.pulse_config)
            member = repo.get_or_create_member(user_id, user_name)
            tool_repo = ToolCenterRepository(session, team.id)
            primary_accounts = tool_repo.get_primary_accounts_for_member(member.id)
            screenshot_account = pick_account_for_screenshot(primary_accounts)
            vendor_slug = (
                screenshot_account.vendor.slug
                if screenshot_account and screenshot_account.vendor
                else "cursor"
            )
        finally:
            session.close()

        if (
            llm.vision_enabled
            and client
            and vendor_slug in ("zhipu", "minimax", "codex")
        ):
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
            except Exception:
                logger.exception("Vendor vision extraction failed")

        if primary_accounts and len(primary_accounts) > 1 and not screenshot_account:
            vendors = sorted({a.vendor.name for a in primary_accounts if a.vendor})
            reply = (
                "📷 截图已收到。您有多个工具账号，无法自动判断工具类型。\n\n"
                f"请使用手工上报：上报 <工具> <数值>\n"
                f"可选工具：{'、'.join(vendors)}\n"
                "或发送 Cursor usage-events.csv 文件。"
            )
            if is_group:
                self.reply_text(format_group_ack(user_name), incoming)
                self.messenger.send_oto_text(user_id, reply)
            else:
                self.reply_text(reply, incoming)
            return

        if llm.vision_enabled and client:
            try:
                result = extract_usage_from_screenshot(
                    dest, client, model=llm.vision_model
                )
                threshold = llm.confidence_threshold
                if result.confidence >= threshold and result.records:
                    parsed = ParsedCsv(records=result.records, summary=result.summary)
                    await self._begin_cursor_usage_ingestion(
                        parsed,
                        incoming,
                        user_id,
                        user_name,
                        channel,
                        input_type="screenshot",
                        raw_source=dest,
                        extraction_confidence=result.confidence,
                        extra_notes=[format_extraction_confidence_note(result.confidence)]
                        + result.warnings,
                        status="confirmed",
                    )
                    return
                if (
                    result.records
                    and self.pulse_config.llm.review_low_confidence
                ):
                    parsed = ParsedCsv(records=result.records, summary=result.summary)
                    await self._begin_cursor_usage_ingestion(
                        parsed,
                        incoming,
                        user_id,
                        user_name,
                        channel,
                        input_type="screenshot",
                        raw_source=dest,
                        extraction_confidence=result.confidence,
                        extra_notes=[
                            f"截图识别置信度 {result.confidence:.0%} 低于阈值 {threshold:.0%}，已提交管理员审核。"
                        ]
                        + result.warnings,
                        status="pending_review",
                        notify_admins_review=True,
                    )
                    return
                warn_lines = result.warnings or ["截图内容不完整或置信度不足"]
                reply = (
                    "📷 截图已收到，但自动识别置信度不足，暂未入库。\n\n"
                    f"置信度：{result.confidence:.0%}（阈值 {threshold:.0%}）\n"
                    + "\n".join(f"· {w}" for w in warn_lines)
                    + "\n\n请改用 Cursor Dashboard → Usage → Export CSV 后发送文件。"
                )
                if is_group:
                    self.reply_text(format_group_ack(user_name), incoming)
                    self.messenger.send_oto_text(user_id, reply)
                else:
                    self.reply_text(reply, incoming)
                return
            except Exception:
                logger.exception("Vision extraction failed")

        reply = (
            "📷 截图已收到并保存。\n\n"
            "当前未启用截图识别（需配置 LLM_API_KEY + VISION_ENABLED=true），"
            "请从 Cursor Dashboard → Usage → Export CSV 后发送文件（推荐私聊）。"
        )
        if is_group:
            self.reply_text(format_group_ack(user_name), incoming)
            self.messenger.send_oto_text(user_id, reply)
        else:
            self.reply_text(reply, incoming)

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
                f"✅ {period} {vendor_name} 截图用量已记录\n"
                f"账号：{account.account_identifier}\n"
                f"主指标：{summary['primary_metric_value']} {summary['primary_metric_unit'].upper()}"
                f"{ratio_line}"
                f"\n识别置信度：{result.confidence:.0%}"
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

    async def _submit_parsed(
        self,
        parsed,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        channel: str,
        *,
        input_type: str,
        raw_source: Path | None = None,
        raw_text: str | None = None,
        extraction_confidence: float = 1.0,
        extra_notes: list[str] | None = None,
        status: str = "confirmed",
        notify_admins_review: bool = False,
        account_id: str | None = None,
        allow_proxy: bool = False,
        proxy_target_name: str | None = None,
    ) -> bool:
        session = self.session_factory()
        team, repo = team_repository(session, self.pulse_config)
        try:
            member = repo.get_or_create_member(user_id, user_name)
            default_period = current_period(self.pulse_config)
            tool_repo = ToolCenterRepository(session, team.id)
            account = tool_repo.get_account(account_id) if account_id else None

            raw_dir = Path(self.pulse_config.storage.raw_files_dir)
            period_ingestions = repo.save_split_ingestions(
                member=member,
                parsed=parsed,
                submit_channel=channel,
                default_period=default_period,
                input_type=input_type,
                raw_source=raw_source,
                raw_files_dir=raw_dir if raw_source else None,
                raw_text=raw_text,
                extraction_confidence=extraction_confidence,
                status=status,
                object_storage_config=self.pulse_config.object_storage,
                team_slug=team.slug,
                account_id=account_id,
                upgrade_notify=(
                    self.messenger.send_oto_text,
                    list(self.pulse_config.admin.dingtalk_user_ids),
                )
                if self.pulse_config.admin.dingtalk_user_ids
                else None,
                allow_proxy=allow_proxy,
            )
            repo.commit()

            periods = [p for p, _ in period_ingestions]
            splits = split_parsed_by_period(parsed)
            period_summaries = [(p, splits[p].summary) for p in periods]

            if notify_admins_review and self.pulse_config.admin.dingtalk_user_ids:
                for period, ingestion in period_ingestions:
                    admin_msg = (
                        f"📋 待审摄取 · {member.display_name} · {period}\n"
                        f"ID：{ingestion.id[:8]}\n"
                        f"置信度：{extraction_confidence:.0%}\n"
                        "管理员可回复：确认 {id前8位} / 拒绝 {id前8位}"
                    )
                    for admin_id in self.pulse_config.admin.dingtalk_user_ids:
                        self.messenger.send_oto_text(admin_id, admin_msg)

            warning = ""
            if status == "pending_review":
                warning = "\n\n⏳ 已提交管理员审核，确认后才会计入团队统计。"
            split_notice = format_auto_split_notice(periods, default_period)
            if split_notice:
                warning += "\n\n" + split_notice
            notes = ""
            if extra_notes:
                notes = "\n\n" + "\n".join(extra_notes)

            account_prefix = ""
            if account:
                account_prefix = f"账号：{account.account_identifier}\n"
                if proxy_target_name:
                    account_prefix = (
                        f"已代 {proxy_target_name} 提交\n账号：{account.account_identifier}\n"
                    )
                account_prefix += "\n"

            detail = (
                account_prefix
                + format_split_period_confirmation(user_name, period_summaries, parsed.summary)
                + warning
                + notes
            )
            if account_id:
                pool_lines: list[str] = []
                for period, _ingestion in period_ingestions:
                    usage_sum = session.scalar(
                        select(UsageSummary).where(
                            UsageSummary.account_id == account_id,
                            UsageSummary.period == period,
                        )
                    )
                    if not usage_sum or (
                        not usage_sum.estimated_included_spend_usd and not usage_sum.cursor_pools
                    ):
                        continue
                    note = format_pool_spend_note(
                        pool_spend=float(usage_sum.primary_metric_value),
                        reported_spend=float(usage_sum.reported_spend_usd or 0),
                        estimated_included_spend=float(usage_sum.estimated_included_spend_usd or 0),
                        quota_ratio=usage_sum.quota_usage_ratio,
                        unit=usage_sum.primary_metric_unit,
                        cursor_pools=usage_sum.cursor_pools,
                    ).strip()
                    if len(period_ingestions) > 1:
                        pool_lines.append(f"【{period}】\n{note}")
                    else:
                        pool_lines.append(note)
                if pool_lines:
                    detail += "\n\n" + "\n\n".join(pool_lines)
            if channel == "group" and len(periods) == 1:
                detail += "\n\n" + format_group_submit_private_footer()
            if channel == "group":
                self.reply_text(format_group_ack(user_name), incoming)
                self.messenger.send_oto_text(user_id, detail)
            else:
                self.reply_text(detail, incoming)
            return True
        except Exception as exc:
            logger.exception("Ingestion import failed")
            session.rollback()
            if channel == "group":
                self.reply_text(f"@{user_name} 处理失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, f"导入失败：{exc}")
            else:
                self.reply_text(f"导入失败：{exc}", incoming)
            return False
        finally:
            session.close()

    async def _handle_tip(
        self,
        text: str,
        incoming: dingtalk_stream.ChatbotMessage,
        user_id: str,
        user_name: str,
        is_group: bool,
    ) -> None:
        from pulse.tool_center.knowledge import KnowledgeService

        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.pulse_config)
            member = repo.get_or_create_member(user_id, user_name)
            period = current_period(self.pulse_config)
            channel = "dingtalk_group" if is_group else "dingtalk_dm"
            svc = KnowledgeService(session, team.id, self.pulse_config)
            entry = svc.create_from_raw(
                author=member,
                raw_text=text,
                source_channel=channel,
                period=period,
            )
            repo.commit()
            reply = (
                f"✅ 心得已收录进知识库\n\n"
                f"标题：{entry.title}\n"
                f"标签：{'、'.join(entry.tags or []) or '无'}\n\n"
                "感谢分享！每月精选会发到群里～"
            )
            if is_group:
                self.reply_text("已收到你的心得 ✅ 详细整理结果已私聊发你。", incoming)
                self.messenger.send_oto_text(user_id, reply)
            else:
                self.reply_text(reply, incoming)
        except Exception as exc:
            session.rollback()
            logger.exception("Tip submission failed")
            self.reply_text(f"心得保存失败：{exc}", incoming)
        finally:
            session.close()

    async def _handle_command(self, text: str, incoming: dingtalk_stream.ChatbotMessage, user_id: str) -> None:
        from pulse.bot.commands import run_command

        session = self.session_factory()
        try:
            _team, repo = team_repository(session, self.pulse_config)
            reply = run_command(
                text,
                user_id,
                self.pulse_config,
                repo,
                messenger=self.messenger,
            )
            session.commit()
            self.reply_text(reply, incoming)
        except Exception as exc:
            session.rollback()
            logger.exception("Command failed")
            self.reply_text(f"命令执行失败：{exc}", incoming)
        finally:
            session.close()

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
        self.pulse_config.dingtalk.group_open_conversation_id = open_id
        save_group_binding(
            open_conversation_id=open_id,
            chat_id=self.pulse_config.dingtalk.chat_id or None,
            title=incoming.conversation_title,
        )
        logger.info(
            "已自动绑定群 openConversationId=%s title=%s chat_id=%s",
            open_id,
            incoming.conversation_title,
            self.pulse_config.dingtalk.chat_id,
        )
