from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.repository import AssistantRepository

PRIVATE_IDLE = timedelta(minutes=30)
GROUP_IDLE = timedelta(minutes=10)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _idle_for(conversation_type: str) -> timedelta:
    if conversation_type == "group":
        return GROUP_IDLE
    return PRIVATE_IDLE


def session_key_fields(event: IncomingMessageEvent) -> dict[str, str | None]:
    if event.conversation_type == "private":
        user_id = event.sender_channel_user_id
        conversation_id = user_id
    else:
        user_id = None
        conversation_id = event.conversation_id
    return {
        "assistant_id": event.assistant_id,
        "team_id": event.team_id,
        "channel": event.channel,
        "conversation_type": event.conversation_type,
        "conversation_id": conversation_id,
        "user_id": user_id,
    }


def get_open_session(
    db_session: Session,
    *,
    assistant_id: str,
    team_id: str,
    channel: str,
    conversation_type: str,
    conversation_id: str,
    user_id: str | None = None,
) -> ChatSessionRow | None:
    stmt = select(ChatSessionRow).where(
        ChatSessionRow.assistant_id == assistant_id,
        ChatSessionRow.team_id == team_id,
        ChatSessionRow.channel == channel,
        ChatSessionRow.conversation_type == conversation_type,
        ChatSessionRow.conversation_id == conversation_id,
        ChatSessionRow.status == "open",
    )
    if conversation_type == "private":
        stmt = stmt.where(ChatSessionRow.user_id == user_id)
        # 私聊按 user_id 续聊；conversation_id 历史上可能不一致，取最近活跃会话
        return db_session.scalar(
            stmt.order_by(ChatSessionRow.last_activity_at.desc()).limit(1)
        )
    return db_session.scalar(stmt)


def find_open_session_with_pending(
    db_session: Session,
    *,
    assistant_id: str,
    team_id: str,
    channel: str,
    user_id: str,
) -> ChatSessionRow | None:
    """Return the most recent open private session that still has a pending capability."""
    from assistant_platform.conversation.pending import get_pending_capability

    stmt = (
        select(ChatSessionRow)
        .where(
            ChatSessionRow.assistant_id == assistant_id,
            ChatSessionRow.team_id == team_id,
            ChatSessionRow.channel == channel,
            ChatSessionRow.conversation_type == "private",
            ChatSessionRow.user_id == user_id,
            ChatSessionRow.status == "open",
        )
        .order_by(ChatSessionRow.last_activity_at.desc())
    )
    for row in db_session.scalars(stmt):
        if get_pending_capability(row) is not None:
            return row
    return None


def close_session(
    db_session: Session,
    session_row: ChatSessionRow,
    *,
    reason: str,
    now: datetime | None = None,
    enqueue_close_job: bool = True,
) -> ChatSessionRow:
    effective_now = now or _utcnow()
    session_row.status = "closed"
    session_row.close_reason = reason
    session_row.closed_at = effective_now
    db_session.add(session_row)
    db_session.flush()
    if enqueue_close_job:
        AssistantRepository(db_session).add_job(
            job_type="session.close",
            payload={"session_id": session_row.id},
        )
    return session_row


def attach_user_message(
    db_session: Session,
    event: IncomingMessageEvent,
    *,
    incoming_event_id: str | None = None,
    now: datetime | None = None,
) -> tuple[ChatSessionRow, ChatMessageRow]:
    effective_now = now or _utcnow()
    key = session_key_fields(event)
    open_session = get_open_session(
        db_session,
        assistant_id=key["assistant_id"],
        team_id=key["team_id"],
        channel=key["channel"],
        conversation_type=key["conversation_type"],
        conversation_id=key["conversation_id"],
        user_id=key["user_id"],
    )

    if open_session is not None:
        idle = _idle_for(event.conversation_type)
        if effective_now - _ensure_aware(open_session.last_activity_at) > idle:
            close_session(db_session, open_session, reason="idle_timeout", now=effective_now)
            open_session = None

    if open_session is None:
        new_session_id = str(uuid.uuid4())
        open_session = ChatSessionRow(
            id=new_session_id,
            assistant_id=key["assistant_id"],
            team_id=key["team_id"],
            channel=key["channel"],
            conversation_type=key["conversation_type"],
            conversation_id=key["conversation_id"],
            user_id=key["user_id"],
            status="open",
            prompt_release_id=None,
            opened_at=effective_now,
            last_activity_at=effective_now,
        )
        db_session.add(open_session)
        db_session.flush()
    else:
        open_session.last_activity_at = effective_now
        db_session.add(open_session)
        db_session.flush()

    message_row = ChatMessageRow(
        session_id=open_session.id,
        role="user",
        text_redacted=event.text_redacted,
        secret_refs_json=list(event.secret_refs),
        incoming_event_id=incoming_event_id,
        meta_json={},
        created_at=effective_now,
    )
    db_session.add(message_row)
    db_session.flush()
    return open_session, message_row
