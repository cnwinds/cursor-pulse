from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.orchestrator import process_session_close_job
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.review.auto_review import run_auto_review
from assistant_platform.review.models import HumanReviewRow, SessionReviewRow
from assistant_platform.storage.db import init_assistant_db

TEAM_ID = "team-review"


def _event(*, text: str = "你好") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def _config() -> AssistantConfig:
    return AssistantConfig(team_id=TEAM_ID, memory_enabled=False)


def _closed_session(session, *, with_assistant: str = "收到，有什么可以帮您？"):
    session_row, _ = attach_user_message(session, _event())
    if with_assistant:
        session.add(
            ChatMessageRow(
                session_id=session_row.id,
                role="assistant",
                text_redacted=with_assistant,
            )
        )
        session.flush()
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    return session_row


def test_run_auto_review_scores_healthy_session_at_80():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row = _closed_session(session)
    session.commit()

    review = run_auto_review(session, session_row.id)
    session.commit()

    assert review.score == 80
    assert review.status == "completed"
    assert review.failure_tags_json == []
    session.close()


def test_run_auto_review_penalizes_error_messages():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row = _closed_session(session)
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="error",
            text_redacted="upstream timeout",
        )
    )
    session.commit()

    review = run_auto_review(session, session_row.id)
    assert review.score == 60
    assert "error_messages" in review.failure_tags_json
    session.close()


def test_run_auto_review_penalizes_empty_assistant_reply():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row = _closed_session(session, with_assistant="")
    session.commit()

    review = run_auto_review(session, session_row.id)
    assert review.score == 70
    assert "empty_assistant_reply" in review.failure_tags_json
    session.close()


def test_run_auto_review_penalizes_tool_failure_meta():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row = _closed_session(session)
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="tool",
            text_redacted="quota lookup",
            meta_json={"tags": ["tool_failed"], "tool_status": "failed"},
        )
    )
    session.commit()

    review = run_auto_review(session, session_row.id)
    assert review.score == 65
    assert "tool_failed" in review.failure_tags_json
    session.close()


def test_run_auto_review_queues_human_review_when_score_below_60():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row = _closed_session(session, with_assistant="")
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="error",
            text_redacted="boom",
        )
    )
    session.commit()

    review = run_auto_review(session, session_row.id)
    session.commit()

    assert review.score == 50
    human = session.scalar(
        select(HumanReviewRow).where(HumanReviewRow.session_id == session_row.id)
    )
    assert human is not None
    assert human.reviewer is None
    assert human.notes == "auto_queued"
    session.close()


def test_session_close_job_runs_auto_review():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row = _closed_session(session)
    session.commit()

    process_session_close_job(session, {"session_id": session_row.id}, _config())
    session.commit()

    review = session.scalar(
        select(SessionReviewRow).where(SessionReviewRow.session_id == session_row.id)
    )
    assert review is not None
    assert review.score == 80
    session.close()


def test_idle_close_enqueues_review_via_session_close_job():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session_row, _ = attach_user_message(session, _event())
    close_session(session, session_row, reason="idle_timeout")
    session.commit()

    from assistant_platform.storage.models import BackgroundJobRow

    close_job = session.scalar(
        select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.close")
    )
    assert close_job is not None

    process_session_close_job(session, close_job.payload_json, _config())
    session.commit()

    review = session.scalar(
        select(SessionReviewRow).where(SessionReviewRow.session_id == session_row.id)
    )
    assert review is not None
    session.close()
