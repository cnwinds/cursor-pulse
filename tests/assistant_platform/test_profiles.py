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
from tests.assistant_actor_helpers import signed_actor_headers
from tests.conftest import SessionFactoryProxy

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-profiles"


def _headers(*, channel_user_id: str = "u1") -> dict[str, str]:
    return signed_actor_headers(
        SERVICE_TOKEN,
        member_id="mem-1",
        role="operator",
        channel_user_id=channel_user_id,
        permissions="assistant:sessions:read:self",
    )


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


@pytest.fixture(scope="module")
def _profiles_app():
    cfg = AssistantConfig(
        service_token=SERVICE_TOKEN,
        team_id=TEAM_ID,
        memory_enabled=True,
        apply_team_settings_overrides=False,
    )
    proxy = SessionFactoryProxy()
    proxy.bind(init_assistant_db("sqlite://", team_id=TEAM_ID))
    client = TestClient(create_assistant_app(cfg, proxy))
    return client, cfg, proxy


@pytest.fixture
def client(_profiles_app):
    test_client, cfg, proxy = _profiles_app
    sf = init_assistant_db("sqlite://", team_id=TEAM_ID)
    proxy.bind(sf)
    return test_client, sf


def test_profile_correction_recompiles_effective_profile(client):
    test_client, sf = client
    session = sf()
    session_row, _ = attach_user_message(session, _event(text="偏好: 详细回复"))
    close_session(session, session_row, reason="manual")
    session.commit()

    process_session_close_job(
        session,
        {"session_id": session_row.id},
        AssistantConfig(
            service_token=SERVICE_TOKEN,
            team_id=TEAM_ID,
            memory_enabled=True,
            apply_team_settings_overrides=False,
        ),
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
    assert body["signal_id"] == signal_id
    assert body["correction_text"] == "请保持简洁"
    assert body["effective_profile"]["items"]
    assert body["effective_profile"]["items"][0]["guidance"] == "请保持简洁"

    verify = sf()
    row = verify.scalar(select(ProfileCorrectionRow).where(ProfileCorrectionRow.signal_id == signal_id))
    assert row is not None
    assert row.correction_text == "请保持简洁"
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
        AssistantConfig(
            service_token=SERVICE_TOKEN,
            team_id=TEAM_ID,
            memory_enabled=True,
            apply_team_settings_overrides=False,
        ),
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
        AssistantConfig(
            service_token=SERVICE_TOKEN,
            team_id=TEAM_ID,
            memory_enabled=True,
            apply_team_settings_overrides=False,
        ),
    )
    session.commit()
    session.close()

    response = test_client.get(
        "/api/assistant/v1/profiles/me",
        headers=_headers(channel_user_id="u1"),
        params={"user_id": "u2", "team_id": TEAM_ID},
    )
    assert response.status_code == 403


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
