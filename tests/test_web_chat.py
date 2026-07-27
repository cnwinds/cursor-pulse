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
    owner = bootstrap_portal_owner(
        repo, channel_user_id="admin1", display_name="Admin", password="pass1234"
    )
    repo.commit()
    session.close()
    yield client, config, owner


def test_chat_api(chat_client):
    client, _config, owner = chat_client
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


def test_chat_requires_auth(chat_client):
    client, _, _ = chat_client
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
