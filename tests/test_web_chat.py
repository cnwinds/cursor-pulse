from unittest.mock import patch

import pytest

fastapi = pytest.importorskip("fastapi")

from pulse.config import AppConfig, AssistantMirrorConfig, TenantConfig, WebConfig
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _chat_app():
    config = AppConfig(
        web=WebConfig(admin_token="secret-token", jwt_secret="jwt-test-secret"),
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        ),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def chat_client(_chat_app):
    client, config, proxy = _chat_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    session = sf()
    _team, repo = make_team_repo(session)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin1", display_name="Admin", password="pass1234")
    repo.commit()
    session.close()
    yield client, config, owner, sf, _team.id


def test_chat_api(chat_client):
    client, _config, owner, _sf, _team_id = chat_client
    token = create_access_token(_config, owner)
    with patch(
        "pulse.channels.dingtalk.mirror.mirror_web_message",
        return_value={"session_id": "sess-1"},
    ):
        res = client.post(
            "/api/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "你好"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "accepted"
    assert body["session_id"] == "sess-1"
    assert "reply" in body
    assert isinstance(body["actions"], list)


def test_chat_poll_after_skips_earlier_deliveries(chat_client):
    from pulse.web.portal_chat import store_portal_chat_delivery

    client, config, owner, sf, team_id = chat_client
    session = sf()
    for text in ("旧回复一", "旧回复二"):
        last = store_portal_chat_delivery(session, team_id=team_id, member_id=owner.id, text=text)
    session.commit()
    last_id = last.id
    session.close()

    token = create_access_token(config, owner)
    with patch(
        "pulse.channels.dingtalk.mirror.mirror_web_message",
        return_value={"session_id": "sess-1"},
    ):
        res = client.post(
            "/api/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "我的用量"},
        )
    assert res.status_code == 200
    poll_after = res.json()["poll_after"]
    assert poll_after > last_id

    polled = client.get(
        "/api/chat/messages",
        params={"after": poll_after},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert polled["items"] == []


def test_chat_history_returns_user_and_assistant_messages(chat_client):
    from pulse.web.portal_chat import store_portal_chat_delivery

    client, config, owner, sf, team_id = chat_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    with patch(
        "pulse.channels.dingtalk.mirror.mirror_web_message",
        return_value={"session_id": "sess-1"},
    ):
        assert client.post("/api/chat", headers=headers, json={"message": "我的用量"}).status_code == 200

    session = sf()
    store_portal_chat_delivery(session, team_id=team_id, member_id=owner.id, text="### 你的用量", kind="final")
    session.commit()
    session.close()

    body = client.get("/api/chat/history", headers=headers).json()
    assert [(m["kind"], m["text"]) for m in body["items"]] == [("user", "我的用量"), ("final", "### 你的用量")]
    assert body["last_id"] == body["items"][-1]["id"]


def test_chat_history_limit_keeps_latest_in_order(chat_client):
    from pulse.web.portal_chat import store_portal_chat_delivery

    client, config, owner, sf, team_id = chat_client
    session = sf()
    for i in range(5):
        store_portal_chat_delivery(session, team_id=team_id, member_id=owner.id, text=f"m{i}")
    session.commit()
    session.close()

    token = create_access_token(config, owner)
    body = client.get("/api/chat/history", params={"limit": 2}, headers={"Authorization": f"Bearer {token}"}).json()
    assert [m["text"] for m in body["items"]] == ["m3", "m4"]


def test_chat_mirror_failure_does_not_keep_user_message(chat_client):
    client, config, owner, _sf, _team_id = chat_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    with patch("pulse.channels.dingtalk.mirror.mirror_web_message", side_effect=RuntimeError("down")):
        assert client.post("/api/chat", headers=headers, json={"message": "hi"}).status_code == 502
    assert client.get("/api/chat/history", headers=headers).json()["items"] == []


def test_chat_messages_include_active_stream_drafts(chat_client):
    from pulse.web.portal_chat import upsert_portal_chat_stream

    client, config, owner, sf, team_id = chat_client
    session = sf()
    upsert_portal_chat_stream(session, team_id=team_id, member_id=owner.id, stream_id="s1", text="### 你的")
    session.commit()
    session.close()

    token = create_access_token(config, owner)
    res = client.get("/api/chat/messages", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["streams"] == [{"stream_id": "s1", "text": "### 你的"}]


def test_chat_requires_auth(chat_client):
    client, *_ = chat_client
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
