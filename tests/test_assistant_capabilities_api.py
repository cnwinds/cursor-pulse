from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, AssistantMirrorConfig, TenantConfig, WebConfig
from pulse.storage.models import Base
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

ASSISTANT_TOKEN = "assistant-test-token"
ASSISTANT_BASE = "http://assistant.test"


@pytest.fixture
def api_env():
    config = AppConfig(
        web=WebConfig(jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            base_url=ASSISTANT_BASE,
            service_token=ASSISTANT_TOKEN,
            timeout_seconds=5.0,
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
    auditor = repo.add_member("auditor", "Auditor")
    auditor.portal_role = "auditor"
    auditor.portal_status = "active"
    repo.commit()
    session.close()

    client = TestClient(create_app(config, sf))
    return {
        "client": client,
        "config": config,
        "owner": owner,
        "auditor": auditor,
        "team": team,
        "session_factory": sf,
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_catalog_requires_auth(api_env):
    res = api_env["client"].get("/api/v2/assistant/capabilities/catalog")
    assert res.status_code == 401


def test_catalog_requires_read_permission(api_env):
    token = create_access_token(api_env["config"], api_env["auditor"])
    res = api_env["client"].get("/api/v2/assistant/capabilities/catalog", headers=_headers(token))
    assert res.status_code == 403


def test_catalog_returns_503_when_mirror_unconfigured(api_env):
    api_env["config"].assistant_mirror.base_url = ""
    api_env["config"].assistant_mirror.service_token = ""
    token = create_access_token(api_env["config"], api_env["owner"])
    res = api_env["client"].get("/api/v2/assistant/capabilities/catalog", headers=_headers(token))
    assert res.status_code == 503
    assert "Assistant" in res.json()["detail"]


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_catalog_proxies_to_assistant(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'[{"key":"quota.self.read"}]'
    mock_response.json.return_value = [{"key": "quota.self.read", "version": "1"}]

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.return_value = mock_response
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    res = api_env["client"].get("/api/v2/assistant/capabilities/catalog", headers=_headers(token))

    assert res.status_code == 200
    assert res.json()[0]["key"] == "quota.self.read"
    mock_client.request.assert_called_once()
    args, kwargs = mock_client.request.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/api/assistant/v1/capabilities/catalog")
    headers = kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {ASSISTANT_TOKEN}"
    assert headers["X-Assistant-Token"] == ASSISTANT_TOKEN
    assert headers["X-Pulse-Actor-Member-Id"] == api_env["owner"].id


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_assignments_list_uses_session_team_id(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"[]"
    mock_response.json.return_value = []

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.return_value = mock_response
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    team_id = api_env["team"].id
    res = api_env["client"].get(
        "/api/v2/assistant/capabilities/assignments",
        headers=_headers(token),
    )

    assert res.status_code == 200
    _args, kwargs = mock_client.request.call_args
    assert kwargs["params"] == {"team_id": team_id}


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_assignments_list_ignores_client_team_id(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"[]"
    mock_response.json.return_value = []

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.return_value = mock_response
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    session_team_id = api_env["team"].id
    res = api_env["client"].get(
        "/api/v2/assistant/capabilities/assignments?team_id=other-team-id",
        headers=_headers(token),
    )

    assert res.status_code == 200
    _args, kwargs = mock_client.request.call_args
    assert kwargs["params"] == {"team_id": session_team_id}


def test_create_assignment_requires_write_permission(api_env):
    token = create_access_token(api_env["config"], api_env["auditor"])
    res = api_env["client"].post(
        "/api/v2/assistant/capabilities/assignments",
        headers=_headers(token),
        json={
            "team_id": api_env["team"].id,
            "scope_type": "user_deny",
            "scope_id": "member-1",
            "capability_key": "cursor.key.bind",
        },
    )
    assert res.status_code == 403


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_create_assignment_proxies_post(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"id":"a1"}'
    mock_response.json.return_value = {"id": "a1", "scope_type": "user_deny"}

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.return_value = mock_response
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    body = {
        "team_id": api_env["team"].id,
        "scope_type": "user_deny",
        "scope_id": "member-1",
        "capability_key": "cursor.key.bind",
    }
    res = api_env["client"].post(
        "/api/v2/assistant/capabilities/assignments",
        headers=_headers(token),
        json=body,
    )

    assert res.status_code == 200
    assert res.json()["id"] == "a1"
    args, kwargs = mock_client.request.call_args
    assert args[0] == "POST"
    assert kwargs["json"] == body


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_delete_assignment_proxies(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"deleted":true}'
    mock_response.json.return_value = {"deleted": True, "id": "a1"}

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.return_value = mock_response
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    res = api_env["client"].delete(
        "/api/v2/assistant/capabilities/assignments/a1",
        headers=_headers(token),
    )

    assert res.status_code == 200
    assert res.json()["deleted"] is True
    args, _kwargs = mock_client.request.call_args
    assert args[0] == "DELETE"
    assert args[1].endswith("/api/assistant/v1/capabilities/assignments/a1")


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_resolved_member_proxies_me_endpoint(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"[]"
    mock_response.json.return_value = [{"key": "quota.self.read", "version": "1"}]

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.return_value = mock_response
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    member_id = "member-xyz"
    res = api_env["client"].get(
        f"/api/v2/assistant/capabilities/members/{member_id}/resolved",
        headers=_headers(token),
        params={"role": "ai_member"},
    )

    assert res.status_code == 200
    assert res.json()[0]["key"] == "quota.self.read"
    args, kwargs = mock_client.request.call_args
    assert args[1].endswith("/api/assistant/v1/capabilities/me")
    assert kwargs["params"]["member_id"] == member_id
    assert kwargs["params"]["team_id"] == api_env["team"].id
    assert kwargs["params"]["role"] == "ai_member"


@patch("pulse.web.assistant_capabilities_api.httpx.Client")
def test_unreachable_assistant_returns_503(mock_client_cls, api_env):
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.request.side_effect = httpx.ConnectError("connection refused")
    mock_client_cls.return_value = mock_client

    token = create_access_token(api_env["config"], api_env["owner"])
    res = api_env["client"].get("/api/v2/assistant/capabilities/catalog", headers=_headers(token))

    assert res.status_code == 503
    assert "不可达" in res.json()["detail"]
