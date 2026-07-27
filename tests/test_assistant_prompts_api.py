from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, AssistantMirrorConfig, TenantConfig, WebConfig
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

ASSISTANT_TOKEN = "assistant-test-token"
ASSISTANT_BASE = "http://assistant.test"
RETIRED_DETAIL = "Prompt editing retired; edit files in assistant_platform/prompts/docs"


@pytest.fixture(scope="module")
def _prompts_app():
    config = AppConfig(
        web=WebConfig(jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            base_url=ASSISTANT_BASE,
            service_token=ASSISTANT_TOKEN,
            timeout_seconds=5.0,
        ),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def api_env(_prompts_app):
    client, config, proxy = _prompts_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    session = sf()
    _, repo = make_team_repo(session)
    owner = bootstrap_portal_owner(
        repo, channel_user_id="admin", display_name="Admin", password="x"
    )
    repo.commit()
    session.close()

    return {
        "client": client,
        "config": config,
        "owner": owner,
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@patch("pulse.web.assistant_prompts_api.internal_client")
def test_prompts_list_proxies_file_readonly_endpoint(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"fragments":[]}'
    mock_response.json.return_value = {"fragments": []}
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.request.return_value = mock_response

    token = create_access_token(api_env["config"], api_env["owner"])
    response = api_env["client"].get(
        "/api/v2/assistant/prompts", headers=_headers(token)
    )

    assert response.status_code == 200
    assert response.json() == {"fragments": []}
    assert mock_client.request.call_args.args[:2] == (
        "GET",
        f"{ASSISTANT_BASE}/api/assistant/v1/prompts",
    )


@patch("pulse.web.assistant_prompts_api.internal_client")
def test_prompts_preview_proxies_file_readonly_endpoint(mock_client_cls, api_env):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"markdown":"# preview"}'
    mock_response.json.return_value = {"markdown": "# preview"}
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.request.return_value = mock_response

    token = create_access_token(api_env["config"], api_env["owner"])
    response = api_env["client"].get(
        "/api/v2/assistant/prompts/preview", headers=_headers(token)
    )

    assert response.status_code == 200
    assert response.json() == {"markdown": "# preview"}
    assert mock_client.request.call_args.args[:2] == (
        "GET",
        f"{ASSISTANT_BASE}/api/assistant/v1/prompts/preview",
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/assistant/prompts/fragments",
        "/api/v2/assistant/prompts/releases",
        "/api/v2/assistant/prompts/proposals/proposal-1/approve",
    ],
)
def test_prompt_writes_return_410_without_proxy(api_env, path: str):
    response = api_env["client"].post(path)

    assert response.status_code == 410
    assert response.json()["detail"] == RETIRED_DETAIL
