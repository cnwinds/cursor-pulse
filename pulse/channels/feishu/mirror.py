"""Mirror Feishu inbound text to the Assistant ingest endpoint."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.domain.identity import DEFAULT_ASSISTANT_ID
from assistant_platform.secrets.redact import redact_text
from pulse.channels.dingtalk.mirror import (
    _post_to_assistant,
    _post_to_assistant_async,
    _write_deadletter,
)
from pulse.channels.inbound import InboundMessage
from pulse.config import AppConfig

logger = logging.getLogger(__name__)


def build_event_from_feishu(
    inbound: InboundMessage,
    *,
    config: AppConfig,
    team_id: str,
    actor_member_id: str | None = None,
    actor_role: str | None = None,
) -> IncomingMessageEvent:
    redacted, refs = redact_text(inbound.text or "")
    safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
    is_group = inbound.conversation_type == "group"
    conversation_id = (
        inbound.conversation_id
        if is_group and inbound.conversation_id
        else inbound.channel_user_id
    )
    reply_endpoint: dict = {
        "channel": "feishu",
        "conversation_type": "group" if is_group else "private",
        "conversation_id": str(conversation_id),
        "user_id": str(inbound.channel_user_id),
    }
    if actor_member_id:
        reply_endpoint["member_id"] = actor_member_id
    if actor_role:
        reply_endpoint["role"] = actor_role
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="feishu",
        channel_message_id=str(inbound.message_id or uuid.uuid4()),
        assistant_id=DEFAULT_ASSISTANT_ID,
        team_id=team_id,
        sender_channel_user_id=str(inbound.channel_user_id),
        sender_display_name=str(inbound.display_name or inbound.channel_user_id),
        conversation_type="group" if is_group else "private",
        conversation_id=str(conversation_id),
        reply_endpoint=reply_endpoint,
        text_redacted=redacted,
        secret_refs=safe_refs,
        attachments=[],
        occurred_at=datetime.now(timezone.utc),
        raw_metadata_redacted={},
    )


def _feishu_mirror_payload(
    inbound: InboundMessage,
    *,
    config: AppConfig,
    team_id: str,
    actor_member_id: str | None = None,
    actor_role: str | None = None,
):
    mirror = config.assistant_mirror
    if not mirror.enabled:
        return None
    event = build_event_from_feishu(
        inbound,
        config=config,
        team_id=team_id,
        actor_member_id=actor_member_id,
        actor_role=actor_role,
    )
    url = f"{mirror.base_url.rstrip('/')}/api/assistant/v1/events/messages"
    headers = {"Content-Type": "application/json"}
    if mirror.service_token:
        headers["Authorization"] = f"Bearer {mirror.service_token}"
    payload = {
        "event_id": event.event_id,
        "channel": event.channel,
        "channel_message_id": event.channel_message_id,
        "assistant_id": event.assistant_id,
        "team_id": event.team_id,
        "sender_channel_user_id": event.sender_channel_user_id,
        "sender_display_name": event.sender_display_name,
        "conversation_type": event.conversation_type,
        "conversation_id": event.conversation_id,
        "reply_endpoint": event.reply_endpoint,
        "text_redacted": event.text_redacted,
        "secret_refs": event.secret_refs,
        "attachments": event.attachments,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "raw_metadata_redacted": event.raw_metadata_redacted,
    }
    return mirror, url, headers, payload


def mirror_feishu_message_sync(
    inbound: InboundMessage,
    *,
    config: AppConfig,
    team_id: str,
    actor_member_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    """Sync mirror for lark-oapi callbacks (no event loop)."""
    built = _feishu_mirror_payload(
        inbound,
        config=config,
        team_id=team_id,
        actor_member_id=actor_member_id,
        actor_role=actor_role,
    )
    if built is None:
        return
    mirror, url, headers, payload = built
    try:
        _post_to_assistant(url, payload, headers, mirror)
    except Exception as exc:
        _write_deadletter("feishu", payload, exc)
        if mirror.fail_open:
            logger.exception(
                "Assistant mirror failed after retries (fail-open); wrote dead-letter"
            )
            return
        raise


async def mirror_feishu_message(
    inbound: InboundMessage,
    *,
    config: AppConfig,
    team_id: str,
    actor_member_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    built = _feishu_mirror_payload(
        inbound,
        config=config,
        team_id=team_id,
        actor_member_id=actor_member_id,
        actor_role=actor_role,
    )
    if built is None:
        return
    mirror, url, headers, payload = built
    try:
        await _post_to_assistant_async(url, payload, headers, mirror)
    except Exception as exc:
        _write_deadletter("feishu", payload, exc)
        if mirror.fail_open:
            logger.exception(
                "Assistant mirror failed after retries (fail-open); wrote dead-letter"
            )
            return
        raise
