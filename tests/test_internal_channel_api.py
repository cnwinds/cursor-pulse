from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, InternalApiConfig, TenantConfig
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

INTERNAL_TOKEN = "pulse-internal-test-token"


@pytest.fixture(scope="module")
def _channel_app():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        internal=InternalApiConfig(service_token=INTERNAL_TOKEN),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def api_env(_channel_app):
    client, config, proxy = _channel_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    session = sf()
    team, repo = make_team_repo(session)
    session.close()
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
        "pulse.web.internal_channel_api._get_channel_messenger",
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
        "pulse.web.internal_channel_api._get_channel_messenger",
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
        channel_user_id="web-user",
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

    rows = list_portal_chat_deliveries(session, team_id=team.id, member_id=member.id, after_id=0)
    assert len(rows) == 1
    assert rows[0].text == "6月用量如下"
    assert rows[0].kind == "final"
    session.close()


def test_channel_reply_group_without_config_returns_queued(api_env, monkeypatch):
    messenger = MagicMock()
    messenger.config.dingtalk.group_open_conversation_id = ""
    monkeypatch.setattr(
        "pulse.web.internal_channel_api._get_channel_messenger",
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


def _web_member(api_env):
    import uuid

    from pulse.storage.models import Member

    session = api_env["session_factory"]()
    member = Member(
        id=str(uuid.uuid4()),
        team_id=api_env["team"].id,
        channel_user_id=f"web-{uuid.uuid4().hex[:6]}",
        display_name="Web User",
        portal_role="member",
    )
    session.add(member)
    session.commit()
    member_id = member.id
    session.close()
    return member_id


def _active_streams(api_env, member_id):
    from pulse.tenant.context import team_repository
    from pulse.web.portal_chat import list_active_portal_chat_streams

    session = api_env["session_factory"]()
    try:
        team, _repo = team_repository(session, api_env["config"])
        return [
            (row.stream_id, row.text)
            for row in list_active_portal_chat_streams(session, team_id=team.id, member_id=member_id)
        ]
    finally:
        session.close()


def _post_stream(api_env, member_id, **fields):
    body = {
        "reply_endpoint": {"channel": "web", "member_id": member_id},
        "session_id": "sess-web",
        "stream_id": "s1",
        **fields,
    }
    return api_env["client"].post("/api/internal/v1/channel/stream", json=body, headers=_auth_headers())


def test_channel_stream_upserts_and_discards_web_draft(api_env):
    member_id = _web_member(api_env)
    assert _post_stream(api_env, member_id, text="### 你的").status_code == 200
    assert _post_stream(api_env, member_id, text="### 你的用量").json()["status"] == "ok"
    assert _active_streams(api_env, member_id) == [("s1", "### 你的用量")]

    assert _post_stream(api_env, member_id, done=True).status_code == 200
    assert _active_streams(api_env, member_id) == []


def test_channel_stream_ignores_non_web_channel(api_env):
    response = api_env["client"].post(
        "/api/internal/v1/channel/stream",
        json={"reply_endpoint": {"channel": "dingtalk", "user_id": "u1"}, "stream_id": "s1", "text": "x"},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "noop"


def test_channel_stream_requires_token(api_env):
    response = api_env["client"].post(
        "/api/internal/v1/channel/stream",
        json={"reply_endpoint": {"channel": "web", "member_id": "m"}, "stream_id": "s1", "text": "x"},
    )
    assert response.status_code == 401


def test_channel_reply_web_closes_stream_draft(api_env):
    member_id = _web_member(api_env)
    _post_stream(api_env, member_id, text="### 你的用量（草稿）")
    response = api_env["client"].post(
        "/api/internal/v1/channel/reply",
        json={
            "reply_endpoint": {"channel": "web", "member_id": member_id},
            "text": "### 你的用量",
            "session_id": "sess-web",
            "message_id": "msg-stream-final",
            "kind": "final",
            "stream_id": "s1",
        },
        headers=_auth_headers(),
    )
    assert response.json()["status"] == "sent"
    assert _active_streams(api_env, member_id) == []
