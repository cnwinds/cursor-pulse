from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = session_factory()
    _, repo = make_team_repo(session)
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin", display_name="Admin", password="x"
    )
    auditor = repo.add_member("auditor", "Auditor")
    auditor.portal_role = "auditor"
    auditor.portal_status = "active"
    repo.commit()
    session.close()
    return {
        "client": TestClient(create_app(config, session_factory)),
        "config": config,
        "owner": owner,
        "auditor": auditor,
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("path", "upstream_path"),
    [
        ("/api/v2/assistant/skills", "/api/assistant/v1/skills"),
        ("/api/v2/assistant/skills/cursor.self", "/api/assistant/v1/skills/cursor.self"),
        (
            "/api/v2/assistant/skills/cursor.self/tasks/quota",
            "/api/assistant/v1/skills/cursor.self/tasks/quota",
        ),
        (
            "/api/v2/assistant/skills/help-topics",
            "/api/assistant/v1/skills/help-topics",
        ),
    ],
)
@patch("httpx.Client")
def test_skills_endpoints_proxy_read_requests(
    mock_client_cls, api_env, path: str, upstream_path: str
):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"ok":true}'
    mock_response.json.return_value = {"ok": True}
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.request.return_value = mock_response

    token = create_access_token(api_env["config"], api_env["owner"])
    response = api_env["client"].get(path, headers=_headers(token))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert mock_client.request.call_args.args[:2] == (
        "GET",
        f"{ASSISTANT_BASE}{upstream_path}",
    )
    assert (
        mock_client.request.call_args.kwargs["headers"]["X-Assistant-Token"]
        == ASSISTANT_TOKEN
    )


def test_skills_endpoints_require_skills_read_permission(api_env):
    token = create_access_token(api_env["config"], api_env["auditor"])

    response = api_env["client"].get(
        "/api/v2/assistant/skills", headers=_headers(token)
    )

    assert response.status_code == 403
