from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatMessageRow
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.evaluation.models import EvaluationRunRow
from assistant_platform.evaluation.stub import run_evaluation_stub
from assistant_platform.prompts.seed import get_production_release
from assistant_platform.review.auto_review import run_auto_review
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-eval"


def _event(*, sender: str = "u1", text: str = "测试") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id=sender,
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id=sender,
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def _closed_session(session, *, assistant_text: str = "好的"):
    session_row, _ = attach_user_message(session, _event())
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted=assistant_text,
        )
    )
    session.flush()
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    run_auto_review(session, session_row.id)
    return session_row


@pytest.fixture
def client():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=False)
    sf = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = sf()
    try:
        from tests.assistant_platform.conftest import seed_test_production_release

        seed_test_production_release(session)
        session.commit()
    finally:
        session.close()
    app = create_assistant_app(cfg, sf)
    return TestClient(app), sf


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Permissions": "assistant:prompts:read,assistant:prompts:write",
    }


def test_run_evaluation_stub_compares_closed_sessions(client):
    _, sf = client
    session = sf()
    production = get_production_release(session)
    assert production is not None

    for idx in range(3):
        _closed_session(session, assistant_text=f"reply-{idx}")
    session.commit()

    run_row = run_evaluation_stub(session, production.id, limit=5)
    session.commit()

    assert run_row.status == "completed"
    assert run_row.release_id == production.id
    assert run_row.result_json["session_count"] == 3
    assert "average_score" in run_row.result_json
    assert "comparisons" in run_row.result_json
    session.close()


def test_evaluation_runs_api_creates_run(client):
    test_client, sf = client
    session = sf()
    production = get_production_release(session)
    assert production is not None
    _closed_session(session)
    session.commit()
    session.close()

    resp = test_client.post(
        "/api/assistant/v1/evaluations/runs",
        json={"release_id": production.id, "limit": 5},
        headers=_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["release_id"] == production.id
    assert data["result"]["session_count"] >= 1

    db = sf()
    try:
        run = db.scalar(select(EvaluationRunRow).where(EvaluationRunRow.id == data["id"]))
        assert run is not None
    finally:
        db.close()


def test_evaluation_runs_api_requires_prompt_read(client):
    test_client, sf = client
    session = sf()
    production = get_production_release(session)
    session.close()

    resp = test_client.post(
        "/api/assistant/v1/evaluations/runs",
        json={"release_id": production.id if production else "x", "limit": 5},
        headers={
            "Authorization": f"Bearer {SERVICE_TOKEN}",
            "X-Pulse-Actor-Permissions": "assistant:sessions:read:self",
        },
    )
    assert resp.status_code == 403
