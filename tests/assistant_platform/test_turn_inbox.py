from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.turn_inbox import (
    TurnInbox,
    begin_turn,
    end_turn,
    is_turn_running,
    reschedule_session_after_turn,
    try_schedule_next_turn,
)
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import BackgroundJobRow
from assistant_platform.storage.repository import AssistantRepository

TEAM = "team-turn-inbox"


def _session_row(db) -> ChatSessionRow:
    row = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="open",
        last_activity_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def _user_message(db, session_id: str, *, text: str, message_id: str | None = None) -> ChatMessageRow:
    row = ChatMessageRow(
        id=message_id or str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        text_redacted=text,
    )
    db.add(row)
    db.flush()
    return row


def test_begin_and_end_turn():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    assert not is_turn_running(row)

    begin_turn(db, row, trigger_message_id="msg-1")
    db.commit()
    assert is_turn_running(row)

    pending = end_turn(db, row)
    db.commit()
    assert not is_turn_running(row)
    assert pending == []


def test_pending_message_drain_and_consume():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="trigger", message_id="msg-1")
    begin_turn(db, row, trigger_message_id="msg-1")
    _user_message(db, row.id, text="查6月份的", message_id="msg-2")
    db.commit()

    inbox = TurnInbox(db, row)
    entries = inbox.drain_unconsumed()
    assert len(entries) == 1
    assert entries[0].message_id == "msg-2"
    assert entries[0].text == "查6月份的"

    inbox.mark_consumed("msg-2")
    assert inbox.drain_unconsumed() == []


def test_end_turn_returns_pending_messages():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="trigger", message_id="msg-1")
    begin_turn(db, row, trigger_message_id="msg-1")
    _user_message(db, row.id, text="查6月份的", message_id="msg-2")
    db.commit()

    pending = end_turn(db, row)
    assert len(pending) == 1
    assert pending[0]["message_id"] == "msg-2"


def test_turn_inbox_limits_drain_batch():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="trigger", message_id="msg-trigger")
    begin_turn(db, row, trigger_message_id="msg-trigger")
    for index in range(8):
        _user_message(db, row.id, text=f"text-{index}", message_id=f"msg-{index}")
    db.commit()

    inbox = TurnInbox(db, row, max_per_drain=3)
    batch = inbox.drain_unconsumed()
    assert len(batch) == 3


def test_turn_inbox_sees_messages_from_other_connection():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="trigger", message_id="msg-1")
    begin_turn(db, row, trigger_message_id="msg-1")
    db.commit()

    inbox = TurnInbox(db, row)
    assert inbox.drain_unconsumed() == []

    db2 = Session()
    row2 = db2.get(ChatSessionRow, row.id)
    _user_message(db2, row2.id, text="补充条件", message_id="msg-2")
    db2.commit()

    assert len(inbox.drain_unconsumed()) == 1


def test_try_schedule_next_turn_when_idle():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="hello", message_id="msg-1")
    db.commit()

    repo = AssistantRepository(db)
    assert try_schedule_next_turn(db, row, repo) is True
    db.commit()

    assert is_turn_running(row)
    job = db.scalar(
        select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
    )
    assert job is not None
    assert job.payload_json["message_id"] == "msg-1"


def test_try_schedule_next_turn_when_running_returns_false():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="first", message_id="msg-1")
    _user_message(db, row.id, text="second", message_id="msg-2")
    begin_turn(db, row, trigger_message_id="msg-1")
    db.commit()

    repo = AssistantRepository(db)
    assert try_schedule_next_turn(db, row, repo) is False


def test_try_schedule_next_turn_skips_when_job_already_queued():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="hello", message_id="msg-1")
    repo = AssistantRepository(db)
    assert try_schedule_next_turn(db, row, repo) is True
    db.commit()

    end_turn(db, row)
    db.add(row)
    db.flush()
    assert try_schedule_next_turn(db, row, repo) is False


def test_reschedule_after_turn_commit_closes_race_window():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session_row(db)
    _user_message(db, row.id, text="first", message_id="msg-1")
    begin_turn(db, row, trigger_message_id="msg-1")
    db.commit()

    end_turn(db, row)
    db.add(row)
    db.flush()
    repo = AssistantRepository(db)
    assert try_schedule_next_turn(db, row, repo) is False
    db.commit()

    db2 = Session()
    _user_message(db2, row.id, text="查好了吗", message_id="msg-late")
    db2.commit()

    assert reschedule_session_after_turn(Session, row.id) is True

    db3 = Session()
    job = db3.scalar(
        select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
    )
    assert job is not None
    assert job.payload_json["message_id"] == "msg-late"
