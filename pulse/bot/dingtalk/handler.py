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
from pulse.bot.commands import CURSOR_BIND_GUIDE
from pulse.bot.dingtalk.group_store import save_group_binding
from pulse.bot.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig
from pulse.extract.text_parser import looks_like_usage_csv
from pulse.extract.summary import format_group_ack
from pulse.llm.client import build_llm_client
from pulse.llm.vision import extract_vendor_usage_from_screenshot
from pulse.tool_center.manual import (
    ManualUsageCommand,
    ManualUsageService,
    looks_like_manual_usage,
    pick_account_for_screenshot,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.periods import current_period
from pulse.tool_center.knowledge import looks_like_tip
from pulse.tenant.context import team_repository

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
            or text.startswith("绑定")
            or text.startswith("解绑")
            or looks_like_manual_usage(text)
            or looks_like_query(text)
        ):
            await self._handle_command(text, incoming, user_id)
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
                "收到文件但无法识别或下载。Cursor 请绑定 API Key；其他工具请发送用量 CSV/Excel。",
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
                f"请绑定 Cursor API Key 自动同步用量，或发送非 Cursor 工具的用量文件。\n\n{CURSOR_BIND_GUIDE}",
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
