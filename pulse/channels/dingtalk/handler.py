from __future__ import annotations

import logging
from pathlib import Path

try:
    import dingtalk_stream
    from dingtalk_stream import AckMessage
except ImportError as exc:  # pragma: no cover - exercised when [dingtalk] not installed
    raise ImportError(
        "钉钉渠道需要 dingtalk-stream。请安装：pip install 'cursor-pulse[dingtalk]'"
    ) from exc

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
from pulse.channels.inbound import InboundMessage, dispatch_text_command
from pulse.channels.outbound_ledger import (
    record_outbound_ledger,
    resolve_group_conversation_id,
    resolve_team_id,
    send_oto_and_ledger,
)
from pulse.config import AppConfig
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

    def _team_id(self) -> str | None:
        session = self.session_factory()
        try:
            return resolve_team_id(self.pulse_config, session=session)
        except Exception:
            logger.exception("resolve team_id for outbound ledger failed")
            return None
        finally:
            session.close()

    def _record_local_outbound(
        self,
        *,
        text: str,
        source: str,
        conversation_type: str,
        user_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        team_id = self._team_id()
        if not team_id:
            return
        record_outbound_ledger(
            self.pulse_config,
            team_id=team_id,
            channel="dingtalk",
            conversation_type=conversation_type,
            text=text,
            source=source,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    def reply_text(self, text: str, incoming_message: dingtalk_stream.ChatbotMessage):
        from pulse.channels.dingtalk.messenger import _looks_like_markdown_message

        is_group = incoming_message.conversation_type == "2"
        user_id = incoming_message.sender_staff_id or incoming_message.sender_id
        result = None
        delivered = False
        if _looks_like_markdown_message(text) and incoming_message.session_webhook:
            try:
                self.messenger.reply_session_text(
                    incoming_message.session_webhook,
                    text,
                    at_user_id=incoming_message.sender_staff_id,
                )
                delivered = True
            except Exception:
                logger.exception("markdown session reply failed; falling back to plain text")
                result = super().reply_text(text, incoming_message)
                delivered = True
        else:
            result = super().reply_text(text, incoming_message)
            delivered = True

        if delivered:
            if is_group:
                cid = (
                    incoming_message.conversation_id
                    or resolve_group_conversation_id(self.pulse_config, "dingtalk")
                )
                if cid:
                    self._record_local_outbound(
                        text=text,
                        source="dingtalk.local_reply",
                        conversation_type="group",
                        conversation_id=cid,
                    )
            elif user_id:
                self._record_local_outbound(
                    text=text,
                    source="dingtalk.local_reply",
                    conversation_type="private",
                    user_id=user_id,
                )
        return result

    def _is_admin(self, user_id: str) -> bool:
        from pulse.channels.admin_gate import is_channel_admin

        return is_channel_admin(user_id, self.pulse_config.admin.channel_user_ids)

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
            self.reply_text(f"@{user_name} 已收到，详情见私聊。", incoming)
            team_id = self._team_id()
            send_oto_and_ledger(
                self.pulse_config,
                self.messenger,
                user_id=user_id,
                text=detail,
                source="dingtalk.local_reply",
                team_id=team_id,
                channel="dingtalk",
            )
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
                member = repo.get_or_create_member(user_id, user_name, channel="dingtalk")
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
                    member = repo.get_or_create_member(user_id, user_name, channel="dingtalk")
                    session.commit()
                    actor_role = member.portal_role
                    if actor_role not in ("owner", "operator") and self._is_admin(user_id):
                        actor_role = "owner"
                    await mirror_dingtalk_message(
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

        # 共享命令分发（channel-neutral）：仅当 Assistant 镜像未启用时兜底本地回复
        # 纯文本命令（帮助/额度/绑定解绑Key/借还Key），避免与 Assistant 侧重复回复。
        if text and not self.pulse_config.assistant_mirror.enabled:
            inbound = InboundMessage(
                channel="dingtalk",
                channel_user_id=user_id,
                display_name=user_name,
                text=text,
                conversation_type="group" if is_group else "oto",
                conversation_id=incoming.conversation_id,
                message_id=incoming.message_id,
                raw=incoming,
            )
            reply = dispatch_text_command(
                config=self.pulse_config,
                session_factory=self.session_factory,
                messenger=self.messenger,
                inbound=inbound,
            )
            if reply is not None:
                self._send_user_detail(
                    incoming=incoming,
                    user_id=user_id,
                    user_name=user_name,
                    channel="group" if is_group else "private",
                    detail=reply,
                )
                return

        # 其余文本：已镜像，由 Assistant 经 channel/reply 回复
        if text:
            return

        # Cursor-only: never download usage attachments (CSV/XLSX); just guide bind-key.
        if (
            extract_file_attachment(raw, incoming)
            or incoming_message_type(raw, incoming) == "file"
        ):
            self.reply_text(
                "Cursor 用量请绑定 API Key 自动同步，不再接受文件上传。\n\n"
                f"{CURSOR_BIND_GUIDE}",
                incoming,
            )
            return

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
            member = repo.get_or_create_member(user_id, user_name, channel="dingtalk")
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
        channel = "group" if is_group else "private"
        self._send_user_detail(
            incoming=incoming,
            user_id=user_id,
            user_name=user_name,
            channel=channel,
            detail=(
                "📷 截图已收到。Cursor 用量请绑定 API Key 自动同步。\n\n"
                f"{CURSOR_BIND_GUIDE}"
            ),
        )

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
                        member = repo.get_member_by_channel_user_id(user_id)
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
            team_id = self._team_id()
            send_oto_and_ledger(
                self.pulse_config,
                self.messenger,
                user_id=user_id,
                text=reply,
                source="dingtalk.local_reply",
                team_id=team_id,
                channel="dingtalk",
            )
        else:
            self.reply_text(reply, incoming)

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
