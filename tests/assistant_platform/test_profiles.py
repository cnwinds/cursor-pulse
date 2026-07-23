from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.orchestrator import process_session_close_job
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.profiles.models import ProfileCorrectionRow, ProfileEffectiveRow, ProfileSignalRow
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-profiles"


def _headers(*, channel_user_id: str = "u1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Channel-User-Id": channel_user_id,
        "X-Pulse-Actor-Permissions": "assistant:sessions:read:self",
    }


def _event(*, sender: str = "u1", text: str = "偏好: 我喜欢短句") -> IncomingMessageEvent:
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


@pytest.fixture
def client():
    cfg = AssistantConfig(
        service_token=SERVICE_TOKEN,
        team_id=TEAM_ID,
        memory_enabled=True,
    )
    sf = init_assistant_db("sqlite://", team_id=TEAM_ID)
    app = create_assistant_app(cfg, sf)
    return TestClient(app), sf


def test_session_close_creates_profile_signal(client):
    _, sf = client
    session = sf()
    session_row, _ = attach_user_message(session, _event(text="偏好: 我习惯用 Opus 模型"))
    close_session(session, session_row, reason="manual")
    session.commit()

    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True),
    )
    session.commit()

    signal = session.scalar(select(ProfileSignalRow).where(ProfileSignalRow.user_id == "u1"))
    assert signal is not None
    assert signal.kind == "preference"
    assert signal.confidence >= 0.3
    assert signal.explicitness == "explicit"
    assert session_row.id in signal.source_session_ids_json
    assert "Opus" in signal.content or "偏好" in signal.content
    session.close()


def test_profile_correction_stores(client):
    test_client, sf = client
    session = sf()
    session_row, _ = attach_user_message(session, _event(text="偏好: 深色主题"))
    close_session(session, session_row, reason="manual")
    session.commit()

    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True),
    )
    session.commit()

    signal = session.scalar(select(ProfileSignalRow).where(ProfileSignalRow.user_id == "u1"))
    assert signal is not None
    signal_id = signal.id
    session.close()

    response = test_client.post(
        "/api/assistant/v1/profiles/corrections",
        headers=_headers(),
        json={
            "user_id": "u1",
            "team_id": TEAM_ID,
            "signal_id": signal_id,
            "correction_text": "其实我喜欢浅色主题",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["signal_id"] == signal_id
    assert body["correction_text"] == "其实我喜欢浅色主题"

    verify = sf()
    row = verify.scalar(select(ProfileCorrectionRow).where(ProfileCorrectionRow.signal_id == signal_id))
    assert row is not None
    assert row.correction_text == "其实我喜欢浅色主题"
    verify.close()


def test_profile_correction_recompiles_effective_profile(client):
    test_client, sf = client
    session = sf()
    session_row, _ = attach_user_message(session, _event(text="偏好: 详细回复"))
    close_session(session, session_row, reason="manual")
    session.commit()

    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True),
    )
    session.commit()

    signal = session.scalar(select(ProfileSignalRow).where(ProfileSignalRow.user_id == "u1"))
    assert signal is not None
    signal_id = signal.id
    session.close()

    response = test_client.post(
        "/api/assistant/v1/profiles/corrections",
        headers=_headers(),
        json={
            "user_id": "u1",
            "team_id": TEAM_ID,
            "signal_id": signal_id,
            "correction_text": "请保持简洁",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["effective_profile"]["items"]
    assert body["effective_profile"]["items"][0]["guidance"] == "请保持简洁"

    verify = sf()
    effective = verify.scalar(
        select(ProfileEffectiveRow).where(
            ProfileEffectiveRow.user_id == "u1",
            ProfileEffectiveRow.team_id == TEAM_ID,
        )
    )
    assert effective is not None
    assert effective.snapshot_json["items"][0]["guidance"] == "请保持简洁"
    verify.close()


def test_profiles_me_lists_signals(client):
    test_client, sf = client
    session = sf()
    session_row, _ = attach_user_message(session, _event(text="偏好: 周末不回工作消息"))
    close_session(session, session_row, reason="manual")
    session.commit()
    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True),
    )
    session.commit()
    session.close()

    response = test_client.get(
        "/api/assistant/v1/profiles/me",
        headers=_headers(),
        params={"user_id": "u1", "team_id": TEAM_ID},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "u1"
    assert len(body["signals"]) >= 1
    assert body["signals"][0]["kind"] == "preference"


def test_profiles_me_scoped_to_actor_user(client):
    test_client, sf = client
    session = sf()
    session_row, _ = attach_user_message(session, _event(sender="u2", text="偏好: 其他用户"))
    close_session(session, session_row, reason="manual")
    session.commit()
    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True),
    )
    session.commit()
    session.close()

    response = test_client.get(
        "/api/assistant/v1/profiles/me",
        headers=_headers(channel_user_id="u1"),
        params={"user_id": "u2", "team_id": TEAM_ID},
    )
    assert response.status_code == 403


def test_group_session_does_not_create_user_profile_signal():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    group_event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="group",
        conversation_id="g1",
        text_redacted="群里说点什么",
        occurred_at=datetime.now(timezone.utc),
    )
    session_row, _ = attach_user_message(session, group_event)
    close_session(session, session_row, reason="manual")
    session.commit()

    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(team_id=TEAM_ID, memory_enabled=True),
    )
    session.commit()

    count = session.scalar(select(ProfileSignalRow))
    assert count is None
    session.close()


def test_visibility_context_private_vs_public():
    from assistant_platform.memory.semantic.domain import SourceVisibility, VisibilityContext

    private_ctx = VisibilityContext.private("u1")
    public_ctx = VisibilityContext.public()
    assert private_ctx.is_public() is False
    assert public_ctx.is_public() is True
    assert private_ctx.audience_id == "u1"
    assert public_ctx.audience_id is None

    # Profile signals are user-scoped; group sessions without user_id skip signal creation.
    assert SourceVisibility.PRIVATE.value == "private"
