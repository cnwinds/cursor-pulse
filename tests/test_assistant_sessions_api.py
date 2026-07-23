from __future__ import annotations

from unittest.mock import patch

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
    return {"client": client, "config": config, "owner": owner, "auditor": auditor, "team": team}


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sessions_proxy_requires_auth(api_env):
    res = api_env["client"].get("/api/v2/assistant/sessions")
    assert res.status_code == 401


def test_sessions_proxy_requires_read_permission(api_env):
    token = create_access_token(api_env["config"], api_env["auditor"])
    res = api_env["client"].get("/api/v2/assistant/sessions", headers=_headers(token))
    assert res.status_code == 403


@patch("pulse.web.assistant_sessions_api.httpx.Client")
def test_sessions_proxy_enriches_display_names(mock_client_cls, api_env):
    mock_response = httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": "sess-1",
                    "user_id": "admin",
                    "channel": "dingtalk",
                    "conversation_type": "private",
                    "status": "open",
                }
            ],
            "total": 1,
        },
        request=httpx.Request("GET", f"{ASSISTANT_BASE}/api/assistant/v1/sessions"),
    )
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.request.return_value = mock_response

    token = create_access_token(api_env["config"], api_env["owner"])
    res = api_env["client"].get("/api/v2/assistant/sessions", headers=_headers(token))
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["user_display_name"] == "Admin"


@patch("pulse.web.assistant_sessions_api.httpx.Client")
def test_sessions_proxy_forwards_request(mock_client_cls, api_env):
    mock_response = httpx.Response(
        200,
        json={"items": [], "total": 0},
        request=httpx.Request("GET", f"{ASSISTANT_BASE}/api/assistant/v1/sessions"),
    )
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.request.return_value = mock_response

    token = create_access_token(api_env["config"], api_env["owner"])
    res = api_env["client"].get("/api/v2/assistant/sessions", headers=_headers(token))
    assert res.status_code == 200
    assert res.json()["total"] == 0
    call_kwargs = mock_client.request.call_args.kwargs
    assert call_kwargs["headers"]["X-Pulse-Actor-Member-Id"] == api_env["owner"].id
    assert "assistant:sessions:read:all" in call_kwargs["headers"]["X-Pulse-Actor-Permissions"]
