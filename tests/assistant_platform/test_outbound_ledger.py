"""Tests for outbound ledger session attach + API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.outbound_ledger import record_outbound_message
from assistant_platform.conversation.session_store import PRIVATE_IDLE, ensure_open_session
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-1"


@pytest.fixture
def db():
    Session = init_assistant_db("sqlite://")
    session = Session()
    yield session
    session.close()


def test_ensure_open_session_creates_and_reuses(db):
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    first = ensure_open_session(
        db,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        now=now,
    )
    second = ensure_open_session(
        db,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        now=now + timedelta(minutes=5),
    )
    db.commit()
    assert first.id == second.id
    assert second.last_activity_at == now + timedelta(minutes=5)


def test_ensure_open_session_idle_creates_new(db):
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    first = ensure_open_session(
        db,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        now=now,
    )
    later = ensure_open_session(
        db,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        now=now + PRIVATE_IDLE + timedelta(minutes=1),
    )
    db.commit()
    assert later.id != first.id
    assert first.status == "closed"
    assert later.status == "open"


def test_record_outbound_message_redacts_and_sets_meta(db):
    key = "pka_" + ("x" * 32)
    session_row, message_row = record_outbound_message(
        db,
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        user_id="u-borrower",
        text=f"Key：{key}",
        source="key_loan.issued",
    )
    db.commit()
    assert key not in message_row.text_redacted
    assert message_row.role == "assistant"
    assert message_row.meta_json["source"] == "key_loan.issued"
    assert message_row.meta_json["proactive"] is True
    assert message_row.meta_json["kind"] == "notify"
    assert session_row.user_id == "u-borrower"


def test_ledger_outbound_api():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    sf = init_assistant_db("sqlite://")
    client = TestClient(create_assistant_app(cfg, sf))

    resp = client.post(
        "/api/assistant/v1/ledger/outbound",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        json={
            "team_id": TEAM_ID,
            "channel": "dingtalk",
            "conversation_type": "private",
            "user_id": "staff-1",
            "text": "临时 Key 已生效",
            "source": "key_loan.issued",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "recorded"
    assert body["meta_json"]["source"] == "key_loan.issued"

    session = sf()
    try:
        rows = list(session.scalars(select(ChatMessageRow)))
        assert len(rows) == 1
        assert rows[0].text_redacted == "临时 Key 已生效"
        sessions = list(session.scalars(select(ChatSessionRow)))
        assert len(sessions) == 1
        assert sessions[0].conversation_type == "private"
    finally:
        session.close()


def test_ledger_outbound_api_requires_token():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    sf = init_assistant_db("sqlite://")
    client = TestClient(create_assistant_app(cfg, sf))
    resp = client.post(
        "/api/assistant/v1/ledger/outbound",
        json={
            "team_id": TEAM_ID,
            "channel": "dingtalk",
            "conversation_type": "private",
            "user_id": "staff-1",
            "text": "hi",
            "source": "x",
        },
    )
    assert resp.status_code in (401, 403)
