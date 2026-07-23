from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.domain.identity import DEFAULT_ASSISTANT_ID
from assistant_platform.secrets.redact import redact_text
from pulse.config import AppConfig

logger = logging.getLogger(__name__)

_MIRROR_RETRY_ATTEMPTS = 3
_MIRROR_RETRY_BACKOFF_SECONDS = 0.4
_MIRROR_MIN_TIMEOUT_SECONDS = 10.0
_DEADLETTER_PATH = Path("data/assistant_mirror_deadletter.jsonl")


def _post_to_assistant(
    url: str, payload: dict, headers: dict, mirror
) -> httpx.Response:
    """POST to assistant ingest with retries so transient failures don't drop messages."""
    timeout = max(float(mirror.timeout_seconds), _MIRROR_MIN_TIMEOUT_SECONDS)
    last_exc: Exception | None = None
    for attempt in range(_MIRROR_RETRY_ATTEMPTS):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp
        except Exception as exc:  # noqa: BLE001 — retry any transient failure
            last_exc = exc
            if attempt < _MIRROR_RETRY_ATTEMPTS - 1:
                time.sleep(_MIRROR_RETRY_BACKOFF_SECONDS * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _write_deadletter(kind: str, payload: dict, error: Exception) -> None:
    """Persist an undelivered mirror payload so no user message is silently lost."""
    try:
        _DEADLETTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "kind": kind,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error": repr(error),
            "payload": payload,
        }
        with _DEADLETTER_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to write assistant mirror dead-letter record")


def build_event_from_dingtalk(
    incoming,
    *,
    text: str,
    config: AppConfig,
    team_id: str,
    is_group: bool,
    actor_member_id: str | None = None,
    actor_role: str | None = None,
) -> IncomingMessageEvent:
    redacted, refs = redact_text(text or "")
    safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
    sender = incoming.sender_staff_id or incoming.sender_id or ""
    # 私聊会话键固定为发送人，避免钉钉 conversation_id 不稳定导致续聊丢 pending
    conversation_id = sender if not is_group else (incoming.conversation_id or sender)
    reply_endpoint: dict = {
        "channel": "dingtalk",
        "conversation_type": "group" if is_group else "private",
        "conversation_id": str(conversation_id),
        "user_id": str(sender),
    }
    if actor_member_id:
        reply_endpoint["member_id"] = actor_member_id
    if actor_role:
        reply_endpoint["role"] = actor_role
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(incoming.message_id or uuid.uuid4()),
        assistant_id=DEFAULT_ASSISTANT_ID,
        team_id=team_id,
        sender_channel_user_id=str(sender),
        sender_display_name=str(incoming.sender_nick or sender),
        conversation_type="group" if is_group else "private",
        conversation_id=str(conversation_id),
        reply_endpoint=reply_endpoint,
        text_redacted=redacted,
        secret_refs=safe_refs,
        attachments=[],
        occurred_at=datetime.now(timezone.utc),
        raw_metadata_redacted={
            "conversation_title": getattr(incoming, "conversation_title", None),
        },
    )


def mirror_dingtalk_message(
    incoming,
    *,
    text: str,
    config: AppConfig,
    team_id: str,
    is_group: bool,
    actor_member_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    mirror = config.assistant_mirror
    if not mirror.enabled:
        return
    event = build_event_from_dingtalk(
        incoming,
        text=text,
        config=config,
        team_id=team_id,
        is_group=is_group,
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
    try:
        _post_to_assistant(url, payload, headers, mirror)
    except Exception as exc:
        _write_deadletter("dingtalk", payload, exc)
        if mirror.fail_open:
            logger.exception(
                "Assistant mirror failed after retries (fail-open); wrote dead-letter"
            )
            return
        raise


def build_event_from_web(
    *,
    message: str,
    config: AppConfig,
    team_id: str,
    member_id: str,
    display_name: str,
    channel_user_id: str,
    actor_role: str | None = None,
) -> IncomingMessageEvent:
    redacted, refs = redact_text(message or "")
    safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
    reply_endpoint = {
        "channel": "web",
        "conversation_type": "private",
        "conversation_id": member_id,
        "user_id": channel_user_id,
        "member_id": member_id,
    }
    if actor_role:
        reply_endpoint["role"] = actor_role
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="web",
        channel_message_id=str(uuid.uuid4()),
        assistant_id=DEFAULT_ASSISTANT_ID,
        team_id=team_id,
        sender_channel_user_id=channel_user_id,
        sender_display_name=display_name,
        conversation_type="private",
        conversation_id=member_id,
        reply_endpoint=reply_endpoint,
        text_redacted=redacted,
        secret_refs=safe_refs,
        attachments=[],
        occurred_at=datetime.now(timezone.utc),
        raw_metadata_redacted={"portal": True},
    )


def mirror_web_message(
    *,
    message: str,
    config: AppConfig,
    team_id: str,
    member_id: str,
    display_name: str,
    channel_user_id: str,
    actor_role: str | None = None,
) -> dict[str, str | None]:
    mirror = config.assistant_mirror
    if not mirror.enabled:
        return {"session_id": None, "message_id": None}
    event = build_event_from_web(
        message=message,
        config=config,
        team_id=team_id,
        member_id=member_id,
        display_name=display_name,
        channel_user_id=channel_user_id,
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
    try:
        resp = _post_to_assistant(url, payload, headers, mirror)
        body = resp.json()
        if isinstance(body, dict):
            return {
                "session_id": body.get("session_id"),
                "message_id": body.get("message_id"),
            }
        return {"session_id": None, "message_id": None}
    except Exception as exc:
        _write_deadletter("web", payload, exc)
        if mirror.fail_open:
            logger.exception(
                "Assistant web mirror failed after retries (fail-open); wrote dead-letter"
            )
            return {"session_id": None, "message_id": None}
        raise
