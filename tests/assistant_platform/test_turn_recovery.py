from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.turn_inbox import begin_turn, is_turn_running
from assistant_platform.conversation.turn_recovery import recover_stale_turn_if_needed
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import BackgroundJobRow

TEAM = "team-recovery"


def _session(db) -> ChatSessionRow:
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


def test_recover_stale_turn_resets_and_requeues():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    row = _session(db)
    trigger = ChatMessageRow(
        id="msg-1",
        session_id=row.id,
        role="user",
        text_redacted="first",
        secret_refs_json=[],
        meta_json={},
    )
    db.add(trigger)
    db.flush()
    begin_turn(db, row, trigger_message_id="msg-1")
    state = dict(row.session_state_json or {})
    started = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    state["turn"]["started_at"] = started
    row.session_state_json = state
    msg = ChatMessageRow(
        id="msg-2",
        session_id=row.id,
        role="user",
        text_redacted="补充",
        secret_refs_json=[],
        meta_json={},
    )
    db.add(msg)
    db.commit()

    assert is_turn_running(row)
    assert recover_stale_turn_if_needed(db, row, timeout_seconds=60) is True
    db.commit()

    db.refresh(row)
    job = db.scalar(
        select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
    )
    assert job is not None
    assert job.payload_json["message_id"] == "msg-2"
    assert is_turn_running(row)
