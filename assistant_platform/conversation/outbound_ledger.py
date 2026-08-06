"""Proactive / Pulse-outbound messages into the conversation ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_store import ensure_open_session
from assistant_platform.domain.identity import DEFAULT_ASSISTANT_ID
from assistant_platform.secrets.redact import redact_text
from assistant_platform.storage.repository import AssistantRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def append_outbound_assistant_message(
    db_session: Session,
    session_row: ChatSessionRow,
    text: str,
    *,
    source: str,
    kind: str = "notify",
    secret_refs: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> ChatMessageRow:
    """Append an assistant message representing a Pulse-originated outbound send."""
    effective_now = now or _utcnow()
    meta: dict[str, Any] = {
        "kind": kind or "notify",
        "source": source,
        "proactive": True,
    }
    message_row = ChatMessageRow(
        session_id=session_row.id,
        role="assistant",
        text_redacted=text,
        secret_refs_json=list(secret_refs or []),
        meta_json=meta,
        created_at=effective_now,
    )
    db_session.add(message_row)
    session_row.last_activity_at = effective_now
    db_session.add(session_row)
    db_session.flush()
    return message_row


def record_outbound_message(
    db_session: Session,
    *,
    team_id: str,
    channel: str,
    conversation_type: str,
    text: str,
    source: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    kind: str = "notify",
    assistant_id: str = DEFAULT_ASSISTANT_ID,
    now: datetime | None = None,
) -> tuple[ChatSessionRow, ChatMessageRow]:
    """Redact, ensure open session, append outbound assistant message."""
    if conversation_type not in ("private", "group"):
        raise ValueError(f"unsupported conversation_type: {conversation_type}")

    if conversation_type == "private":
        if not (user_id or "").strip():
            raise ValueError("user_id required for private outbound")
        uid = user_id.strip()
        cid = uid
        resolved_user_id: str | None = uid
    else:
        cid = (conversation_id or "").strip()
        if not cid:
            raise ValueError("conversation_id required for group outbound")
        resolved_user_id = None

    AssistantRepository(db_session).ensure_assistant(assistant_id)

    text_redacted, refs = redact_text(text or "")
    safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]

    session_row = ensure_open_session(
        db_session,
        assistant_id=assistant_id,
        team_id=team_id,
        channel=channel,
        conversation_type=conversation_type,
        conversation_id=cid,
        user_id=resolved_user_id,
        now=now,
    )
    message_row = append_outbound_assistant_message(
        db_session,
        session_row,
        text_redacted,
        source=source,
        kind=kind,
        secret_refs=safe_refs,
        now=now,
    )
    return session_row, message_row
