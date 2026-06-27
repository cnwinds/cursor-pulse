from __future__ import annotations

import logging
from pathlib import Path

import dingtalk_stream
from dingtalk_stream import AckMessage

from pulse.bot.dingtalk.files import (
    extract_file_attachment,
    extract_picture_download_code,
    inbox_dest,
    normalize_incoming_text,
)
from pulse.bot.dingtalk.group_store import save_group_binding
from pulse.bot.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig
from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.extract.text_parser import looks_like_usage_csv
from pulse.domain import ParsedCsv
from pulse.extract.summary import (
    format_extraction_confidence_note,
    format_group_ack,
    format_period_mismatch_warning,
    format_private_confirmation,
    format_private_confirmation_with_hint,
)
from pulse.llm.client import build_llm_client
from pulse.llm.vision import extract_usage_from_screenshot
from pulse.periods import current_period
from pulse.query.engine import looks_like_query
from pulse.storage.repository import Repository
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
            or looks_like_query(text)
        ):
            await self._handle_command(text, incoming, user_id)
            return

        file_path = self._download_attachment(incoming, raw)
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
            parsed = parse_usage_events_csv(file_path)
        except Exception as exc:
            if channel == "group":
                self.reply_text(f"@{user_name} 解析失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, f"CSV 解析失败：{exc}")
            else:
                self.reply_text(f"CSV 解析失败：{exc}", incoming)
            return
        await self._submit_parsed(
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
        await self._submit_parsed(
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
        if llm.vision_enabled and client:
            try:
                result = extract_usage_from_screenshot(
                    dest, client, model=llm.vision_model
                )
                threshold = llm.confidence_threshold
                if result.confidence >= threshold and result.records:
                    parsed = ParsedCsv(records=result.records, summary=result.summary)
                    await self._submit_parsed(
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
                    await self._submit_parsed(
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
    ) -> None:
        session = self.session_factory()
        team, repo = team_repository(session, self.pulse_config)
        try:
            member = repo.get_or_create_member(user_id, user_name)
            period = current_period(self.pulse_config)

            raw_dir = Path(self.pulse_config.storage.raw_files_dir)
            submission = repo.save_submission(
                member=member,
                period=period,
                parsed=parsed,
                submit_channel=channel,
                input_type=input_type,
                raw_source=raw_source,
                raw_files_dir=raw_dir if raw_source else None,
                raw_text=raw_text,
                extraction_confidence=extraction_confidence,
                status=status,
                object_storage_config=self.pulse_config.object_storage,
                team_slug=team.slug,
            )
            repo.commit()

            if notify_admins_review and self.pulse_config.admin.dingtalk_user_ids:
                admin_msg = (
                    f"📋 待审提交 · {member.display_name} · {period}\n"
                    f"ID：{submission.id[:8]}\n"
                    f"置信度：{extraction_confidence:.0%}\n"
                    "管理员可回复：确认 {id前8位} / 拒绝 {id前8位}"
                )
                for admin_id in self.pulse_config.admin.dingtalk_user_ids:
                    self.messenger.send_oto_text(admin_id, admin_msg)

            warning = ""
            if status == "pending_review":
                warning = "\n\n⏳ 已提交管理员审核，确认后才会计入团队统计。"
            if parsed.summary.period_hint and parsed.summary.period_hint != period:
                warning += "\n\n" + format_period_mismatch_warning(period, parsed.summary)
            notes = ""
            if extra_notes:
                notes = "\n\n" + "\n".join(extra_notes)

            if channel == "group":
                self.reply_text(format_group_ack(user_name), incoming)
                detail = (
                    format_private_confirmation_with_hint(user_name, period, parsed.summary)
                    + warning
                    + notes
                )
                self.messenger.send_oto_text(user_id, detail)
            else:
                detail = format_private_confirmation(user_name, period, parsed.summary) + warning + notes
                self.reply_text(detail, incoming)
        except Exception as exc:
            logger.exception("Submission import failed")
            session.rollback()
            if channel == "group":
                self.reply_text(f"@{user_name} 处理失败，请查看私聊。", incoming)
                self.messenger.send_oto_text(user_id, f"导入失败：{exc}")
            else:
                self.reply_text(f"导入失败：{exc}", incoming)
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
    ) -> Path | None:
        attachment = extract_file_attachment(raw)
        if attachment:
            file_name, download_code = attachment
            if not file_name.lower().endswith(".csv"):
                logger.warning("Ignored non-csv file: %s", file_name)
                return None
            dest = inbox_dest(Path(self.pulse_config.storage.raw_files_dir), file_name)
            return self.messenger.download_message_file(download_code, dest)

        # 图片已在上方单独处理
        return None

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
