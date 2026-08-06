"""Adapts Feishu inbound events to the channel-neutral dispatch path.

Decoupled from the ``lark-oapi`` event object shape as much as possible:
``build_inbound_from_feishu_event`` only relies on duck-typed attribute
access (``event.event.message`` / ``event.event.sender``), so it works with
both the real SDK's ``P2ImMessageReceiveV1`` and simple test doubles.

Behavior mirrors DingTalkChannelHandler for text:
- When Assistant mirror is enabled → ingest and let Assistant reply via
  ``/api/internal/v1/channel/reply``
- Otherwise → local ``dispatch_text_command`` and send reply immediately
"""

from __future__ import annotations

import json
import logging

from pulse.channels.admin_gate import is_channel_admin
from pulse.channels.feishu.messenger import FeishuMessenger
from pulse.channels.feishu.mirror import mirror_feishu_message_sync
from pulse.channels.inbound import InboundMessage, dispatch_text_command
from pulse.config import AppConfig
from pulse.tenant.context import team_repository

logger = logging.getLogger(__name__)


def _extract_text(content_json: str | None) -> str:
    if not content_json:
        return ""
    try:
        data = json.loads(content_json)
    except (TypeError, ValueError):
        return ""
    text = data.get("text") if isinstance(data, dict) else None
    return (text or "").strip()


def build_inbound_from_feishu_event(event: object) -> InboundMessage | None:
    """event: lark_oapi.im.v1.P2ImMessageReceiveV1-shaped object (duck-typed)."""
    inner = getattr(event, "event", None)
    if inner is None:
        return None
    message = getattr(inner, "message", None)
    sender = getattr(inner, "sender", None)
    if message is None or sender is None:
        return None
    if getattr(message, "message_type", None) != "text":
        return None

    text = _extract_text(getattr(message, "content", None))
    if not text:
        return None

    sender_id = getattr(sender, "sender_id", None)
    open_id = (
        getattr(sender_id, "open_id", None)
        or getattr(sender_id, "user_id", None)
        or getattr(sender_id, "union_id", None)
        or ""
    )
    chat_type = getattr(message, "chat_type", "p2p")
    conversation_type = "group" if chat_type == "group" else "oto"

    return InboundMessage(
        channel="feishu",
        channel_user_id=str(open_id),
        display_name=str(open_id),
        text=text,
        conversation_type=conversation_type,
        conversation_id=getattr(message, "chat_id", None),
        message_id=getattr(message, "message_id", None),
        raw=event,
    )


class FeishuEventHandler:
    """Bridges Feishu WS/webhook events → mirror or shared dispatch → reply."""

    def __init__(self, config: AppConfig, session_factory, messenger: FeishuMessenger):
        self.config = config
        self.session_factory = session_factory
        self.messenger = messenger

    def _is_admin(self, user_id: str) -> bool:
        return is_channel_admin(user_id, self.config.admin.channel_user_ids)

    def handle_message_receive(self, event: object) -> None:
        inbound = build_inbound_from_feishu_event(event)
        if inbound is None:
            return
        if self.config.feishu.bot_open_id and inbound.channel_user_id == self.config.feishu.bot_open_id:
            return  # ignore the bot's own messages

        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.config)
            member = repo.get_or_create_member(
                inbound.channel_user_id,
                inbound.display_name,
                channel="feishu",
            )
            actor_role = member.portal_role
            if actor_role not in ("owner", "operator") and self._is_admin(inbound.channel_user_id):
                actor_role = "owner"
            session.commit()
            team_id = team.id
            actor_member_id = member.id
        except Exception:
            session.rollback()
            logger.exception("Feishu member resolve failed")
            return
        finally:
            session.close()

        if self.config.assistant_mirror.enabled:
            try:
                mirror_feishu_message_sync(
                    inbound,
                    config=self.config,
                    team_id=team_id,
                    actor_member_id=actor_member_id,
                    actor_role=actor_role,
                )
            except Exception:
                logger.exception("Feishu assistant mirror failed")
            return

        try:
            reply = dispatch_text_command(
                config=self.config,
                session_factory=self.session_factory,
                messenger=self.messenger,
                inbound=inbound,
            )
        except Exception:
            logger.exception("Feishu dispatch_text_command failed")
            reply = "处理失败，请稍后重试或联系管理员。"

        if not reply:
            return

        try:
            from pulse.channels.base import messenger_delivered
            from pulse.channels.outbound_ledger import record_outbound_ledger, send_oto_and_ledger

            if inbound.conversation_id:
                result = self.messenger.send_text_to_chat(inbound.conversation_id, reply)
                if messenger_delivered(result):
                    if inbound.conversation_type == "group":
                        record_outbound_ledger(
                            self.config,
                            team_id=team_id,
                            channel="feishu",
                            conversation_type="group",
                            conversation_id=inbound.conversation_id,
                            text=reply,
                            source="feishu.local_reply",
                        )
                    else:
                        record_outbound_ledger(
                            self.config,
                            team_id=team_id,
                            channel="feishu",
                            conversation_type="private",
                            user_id=inbound.channel_user_id,
                            text=reply,
                            source="feishu.local_reply",
                        )
            else:
                send_oto_and_ledger(
                    self.config,
                    self.messenger,
                    user_id=inbound.channel_user_id,
                    text=reply,
                    source="feishu.local_reply",
                    team_id=team_id,
                    channel="feishu",
                )
        except Exception:
            logger.exception("Feishu reply send failed")
