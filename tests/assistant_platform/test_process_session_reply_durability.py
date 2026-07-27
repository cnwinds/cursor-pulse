"""Final reply must survive end_turn lock failures (no silent no-reply)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.models import ChatMessageRow
from assistant_platform.conversation.orchestrator import process_session_job
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import BackgroundJobRow

TEAM = "team-reply-durable"


def _event(text: str = "我的用量") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def test_end_turn_lock_after_reply_does_not_drop_final_message(monkeypatch):
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    session_row, user_msg = attach_user_message(db, _event())
    db.commit()

    monkeypatch.setattr(
        "assistant_platform.conversation.orchestrator.generate_reply_text",
        lambda *a, **k: "用量合计：1 美元",
    )

    calls = {"n": 0}
    import assistant_platform.conversation.orchestrator as orch

    real_end = orch.end_turn

    def flaky_end_turn(db_session, row):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return real_end(db_session, row)

    monkeypatch.setattr(orch, "end_turn", flaky_end_turn)

    process_session_job(
        db,
        {
            "session_id": session_row.id,
            "message_id": user_msg.id,
            "incoming_event_id": None,
        },
        AssistantConfig(
            team_id=TEAM,
            apply_team_settings_overrides=False,
            llm=AssistantLlmConfig(enabled=False),
        ),
    )
    db.commit()

    finals = list(
        db.scalars(
            select(ChatMessageRow).where(
                ChatMessageRow.session_id == session_row.id,
                ChatMessageRow.role == "assistant",
            )
        ).all()
    )
    assert any(m.text_redacted == "用量合计：1 美元" for m in finals), finals
    reply_jobs = list(
        db.scalars(
            select(BackgroundJobRow).where(BackgroundJobRow.job_type == "reply.send")
        ).all()
    )
    assert reply_jobs, "reply.send job must remain after end_turn lock recovery"
    assert calls["n"] >= 2


def test_process_session_job_skips_duplicate_after_committed_final(monkeypatch):
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    session_row, user_msg = attach_user_message(db, _event())
    db.commit()

    calls = {"n": 0}

    def once(*_a, **_k):
        calls["n"] += 1
        return f"reply-{calls['n']}"

    monkeypatch.setattr(
        "assistant_platform.conversation.orchestrator.generate_reply_text",
        once,
    )
    config = AssistantConfig(
        team_id=TEAM,
        apply_team_settings_overrides=False,
        llm=AssistantLlmConfig(enabled=False),
    )
    payload = {
        "session_id": session_row.id,
        "message_id": user_msg.id,
        "incoming_event_id": None,
    }
    process_session_job(db, payload, config)
    db.commit()
    process_session_job(db, payload, config)
    db.commit()

    finals = [
        m
        for m in db.scalars(
            select(ChatMessageRow).where(
                ChatMessageRow.session_id == session_row.id,
                ChatMessageRow.role == "assistant",
            )
        ).all()
        if (m.meta_json or {}).get("kind") == "final"
    ]
    assert len(finals) == 1
    assert finals[0].text_redacted == "reply-1"
    assert (finals[0].meta_json or {}).get("trigger_message_id") == user_msg.id
    assert calls["n"] == 1
