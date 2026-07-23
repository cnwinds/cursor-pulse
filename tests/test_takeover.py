from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.channels.dingtalk.handler import DingTalkChannelHandler
from pulse.config import (
    AppConfig,
    AssistantMirrorConfig,
    TenantConfig,
    WebConfig,
)
from pulse.storage.models import Base
from pulse.storage.db import init_db
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


def _handler_config() -> AppConfig:
    return AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        ),
    )


def _incoming_private(text: str = "今天天气怎么样") -> MagicMock:
    incoming = MagicMock()
    incoming.conversation_type = "1"
    incoming.is_in_at_list = True
    incoming.sender_staff_id = "u1"
    incoming.sender_id = "u1"
    incoming.sender_nick = "Alice"
    incoming.conversation_id = "u1"
    incoming.message_id = "msg-1"
    incoming.text.content = text
    incoming.message_type = "text"
    return incoming


@pytest.fixture
def bot_env():
    config = _handler_config()
    messenger = MagicMock()
    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    repo.get_or_create_member("u1", "Alice")
    session.commit()

    handler = DingTalkChannelHandler(
        config=config,
        session_factory=lambda: session,
        messenger=messenger,
    )
    return {"handler": handler, "session": session, "team": team, "messenger": messenger}


@pytest.mark.asyncio
async def test_text_only_mirrors_no_local_reply(bot_env):
    handler = bot_env["handler"]
    incoming = _incoming_private("查询 我的用量")
    with patch("pulse.channels.dingtalk.mirror.mirror_dingtalk_message") as mirror:
        await handler._handle_message(incoming, {})
        mirror.assert_called_once()
        kwargs = mirror.call_args.kwargs
        assert kwargs.get("actor_member_id")
    bot_env["messenger"].send_oto_text.assert_not_called()
    # reply_text is on ChatbotHandler; ensure we did not reply for plain text
    with patch.object(handler, "reply_text") as reply:
        with patch("pulse.channels.dingtalk.mirror.mirror_dingtalk_message"):
            await handler._handle_message(_incoming_private("帮助"), {})
        reply.assert_not_called()


@pytest.mark.asyncio
async def test_guide_image_command_still_local(bot_env):
    handler = bot_env["handler"]
    incoming = _incoming_private("设置引导图")
    with (
        patch("pulse.channels.dingtalk.mirror.mirror_dingtalk_message"),
        patch.object(handler, "reply_text") as reply,
        patch("pulse.authz.actor.can_manage_guide_image", return_value=True),
    ):
        await handler._handle_message(incoming, {})
        reply.assert_called_once()
        assert "引导截图" in reply.call_args.args[0]
    assert "u1" in handler._pending_guide_upload


@pytest.fixture
def web_env():
    config = AppConfig(
        web=WebConfig(jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        ),
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
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")
    repo.commit()
    session.close()
    client = TestClient(create_app(config, sf))
    return {"client": client, "config": config, "owner": owner, "team": team}


def test_web_chat_always_mirrors(web_env):
    token = create_access_token(web_env["config"], web_env["owner"])
    with patch("pulse.channels.dingtalk.mirror.mirror_web_message") as mirror:
        response = web_env["client"].post(
            "/api/chat",
            json={"message": "帮我查额度"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert "poll_after" in body
    mirror.assert_called_once()
