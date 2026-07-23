from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.retention import count_expired_messages, purge_messages_older_than
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.memory.archive_indexer import archive_and_index_session
from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.storage.db import init_assistant_db


def _event(msg_id: str) -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id="team-1",
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted="old message",
        occurred_at=datetime.now(timezone.utc),
    )


def test_retention_deletes_messages_older_than_threshold():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    old_time = now - timedelta(days=200)
    session_row, message = attach_user_message(session, _event("old-1"), now=old_time)
    message.created_at = old_time
    # Successfully archive so retention may purge the ledger copy.
    session_row.status = "closed"
    session_row.closed_at = old_time
    session.commit()
    archive_and_index_session(session, session_row, index_version=1)
    session.commit()

    attach_user_message(session, _event("new-1"), now=now)
    session.commit()

    assert count_expired_messages(session, days=180, now=now) == 1

    deleted = purge_messages_older_than(session, days=180, now=now)
    session.commit()

    assert deleted == 1
    remaining = session.scalar(select(func.count()).select_from(ChatMessageRow)) or 0
    assert remaining == 1
    assert count_expired_messages(session, days=180, now=now) == 0
    # Permanent archive stays.
    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    assert archive is not None
    assert archive.archive_status == "ready"
    session.close()


def test_retention_keeps_unarchived_old_messages():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    old_time = now - timedelta(days=200)
    session_row, message = attach_user_message(session, _event("old-unarchived"), now=old_time)
    message.created_at = old_time
    session_row.status = "closed"
    session_row.closed_at = old_time
    session.commit()

    deleted = purge_messages_older_than(session, days=180, now=now)
    session.commit()

    assert deleted == 0
    remaining = session.scalar(select(func.count()).select_from(ChatMessageRow)) or 0
    assert remaining == 1
    session.close()


def test_retention_keeps_messages_when_archive_failed():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    old_time = now - timedelta(days=200)
    session_row = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id="team-1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="closed",
        opened_at=old_time,
        last_activity_at=old_time,
        closed_at=old_time,
    )
    session.add(session_row)
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="user",
            text_redacted="failed archive",
            created_at=old_time,
        )
    )
    session.add(
        SessionArchiveRow(
            session_id=session_row.id,
            team_id="team-1",
            scope="personal",
            subject_id="u1",
            assistant_id="xiaomai",
            channel="dingtalk",
            conversation_type="private",
            conversation_id="u1",
            user_id="u1",
            status="failed",
            archive_status="failed",
            index_status="pending",
            index_version=1,
            message_total=0,
            chunk_total=0,
        )
    )
    session.commit()

    deleted = purge_messages_older_than(session, days=180, now=now)
    session.commit()
    assert deleted == 0
    session.close()
