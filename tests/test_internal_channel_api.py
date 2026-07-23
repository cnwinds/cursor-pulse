from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, InternalApiConfig, TenantConfig
from pulse.storage.models import Base
from pulse.web.app import create_app
from tests.conftest import make_team_repo

INTERNAL_TOKEN = "pulse-internal-test-token"


@pytest.fixture
def api_env():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        internal=InternalApiConfig(service_token=INTERNAL_TOKEN),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = sf()
    team, repo = make_team_repo(session)
    session.close()
    client = TestClient(create_app(config, sf))
    return {"client": client, "config": config, "session_factory": sf, "team": team}


def _auth_headers(token: str | None = INTERNAL_TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _reply_body(*, conversation_type: str = "private", user_id: str = "u1") -> dict:
    return {
        "reply_endpoint": {
            "channel": "dingtalk",
            "conversation_type": conversation_type,
            "conversation_id": "conv-1" if conversation_type == "group" else user_id,
            "user_id": user_id,
        },
        "text": "你好，我是小脉",
        "session_id": "sess-1",
    }


def test_channel_reply_rejects_missing_token(api_env):
    response = api_env["client"].post(
        "/api/internal/v1/channel/reply",
        json=_reply_body(),
    )
    assert response.status_code == 401


def test_channel_reply_rejects_wrong_token(api_env):
    response = api_env["client"].post(
        "/api/internal/v1/channel/reply",
        json=_reply_body(),
        headers=_auth_headers("wrong"),
    )
    assert response.status_code == 401


def test_channel_reply_private_sends_oto_text(api_env, monkeypatch):
    messenger = MagicMock()
    monkeypatch.setattr(
        "pulse.web.internal_channel_api._get_dingtalk_messenger",
        lambda _config: messenger,
    )
    response = api_env["client"].post(
        "/api/internal/v1/channel/reply",
        json=_reply_body(conversation_type="private", user_id="staff-42"),
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    messenger.send_oto_text.assert_called_once_with("staff-42", "你好，我是小脉")


def test_channel_reply_without_messenger_returns_queued(api_env, monkeypatch):
    monkeypatch.setattr(
        "pulse.web.internal_channel_api._get_dingtalk_messenger",
        lambda _config: None,
    )
    response = api_env["client"].post(
        "/api/internal/v1/channel/reply",
        json=_reply_body(),
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_channel_reply_web_stores_delivery(api_env):
    import uuid

    from pulse.config import AppConfig
    from pulse.storage.models import Member
    from pulse.web.internal_channel_api import deliver_channel_reply

    session = api_env["session_factory"]()
    team = api_env["team"]
    member = Member(
        id=str(uuid.uuid4()),
        team_id=team.id,
        dingtalk_user_id="web-user",
        display_name="Web User",
        portal_role="member",
    )
    session.add(member)
    session.commit()

    result = deliver_channel_reply(
        api_env["config"],
        reply_endpoint={"channel": "web", "member_id": member.id},
        text="6月用量如下",
        session=session,
        team_id=team.id,
        assistant_session_id="sess-web",
        assistant_message_id="msg-web",
        kind="final",
    )
    assert result["status"] == "sent"
    session.commit()

    from pulse.web.portal_chat import list_portal_chat_deliveries

    rows = list_portal_chat_deliveries(
        session, team_id=team.id, member_id=member.id, after_id=0
    )
    assert len(rows) == 1
    assert rows[0].text == "6月用量如下"
    assert rows[0].kind == "final"
    session.close()


def test_channel_reply_group_without_config_returns_queued(api_env, monkeypatch):
    messenger = MagicMock()
    messenger.config.dingtalk.group_open_conversation_id = ""
    monkeypatch.setattr(
        "pulse.web.internal_channel_api._get_dingtalk_messenger",
        lambda _config: messenger,
    )
    response = api_env["client"].post(
        "/api/internal/v1/channel/reply",
        json=_reply_body(conversation_type="group"),
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    messenger.send_group_text.assert_not_called()
