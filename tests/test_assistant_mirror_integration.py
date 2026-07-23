from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db
from pulse.channels.dingtalk.mirror import mirror_dingtalk_message
from pulse.config import AppConfig, AssistantMirrorConfig


@pytest.fixture
def assistant_client():
    cfg = AssistantConfig(
        service_token="mirror-test-token",
        team_id="team-mirror",
        apply_team_settings_overrides=False,
    )
    session_factory = init_assistant_db("sqlite://")
    app = create_assistant_app(cfg, session_factory)
    return TestClient(app), session_factory


def test_mirror_posts_to_assistant_and_creates_session(assistant_client):
    client, session_factory = assistant_client
    pulse_cfg = AppConfig(
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="mirror-test-token",
        )
    )
    incoming = MagicMock()
    incoming.message_id = "msg-mirror-1"
    incoming.conversation_type = "1"
    incoming.conversation_id = "staff-1"
    incoming.sender_staff_id = "staff-1"
    incoming.sender_id = "staff-1"
    incoming.sender_nick = "Alice"
    incoming.conversation_title = ""

    def _route_post(url: str, **kwargs):
        path = url.removeprefix("http://assistant.test")
        headers = kwargs.get("headers") or {}
        json_body = kwargs.get("json")
        response = client.post(path, json=json_body, headers=headers)
        routed = MagicMock()
        routed.status_code = response.status_code
        routed.json = response.json
        routed.raise_for_status = response.raise_for_status
        return routed

    with patch("pulse.channels.dingtalk.mirror.httpx.Client") as Client, patch(
        "pulse.channels.dingtalk.mirror.time.sleep"
    ):
        http_client = Client.return_value.__enter__.return_value
        http_client.post.side_effect = _route_post
        mirror_dingtalk_message(
            incoming,
            text="你好小脉",
            config=pulse_cfg,
            team_id="team-mirror",
            is_group=False,
        )

    http_client.post.assert_called_once()
    args, kwargs = http_client.post.call_args
    assert args[0].endswith("/api/assistant/v1/events/messages")
    assert kwargs["headers"]["Authorization"] == "Bearer mirror-test-token"

    with session_factory() as session:
        from assistant_platform.conversation.models import ChatSessionRow
        from sqlalchemy import select

        row = session.scalar(select(ChatSessionRow).where(ChatSessionRow.team_id == "team-mirror"))
        assert row is not None
        assert row.user_id == "staff-1"
