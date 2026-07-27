from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.storage.models import AiVendor
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory, mock_cursor_key_exchange

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture(scope="module")
def _account_create_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def create_env(_account_create_app):
    client, config, proxy = _account_create_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    seed_v2_catalog(s, team)
    other = AiVendor(slug="other", name="Other", website="", is_active=True)
    s.add(other)
    s.flush()
    tool_repo = ToolCenterRepository(s, team.id)
    cursor = tool_repo.get_vendor_by_slug("cursor")
    plan = next(p for p in tool_repo.list_plans(cursor.id))
    repo.commit()
    s.close()

    return {
        "client": client,
        "config": config,
        "owner": owner,
        "cursor_vendor_id": cursor.id,
        "plan_id": plan.id,
        "other_vendor_id": other.id,
        "session_factory": sf,
        "team_id": team.id,
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mock_cursor_client(*, email: str):
    mock_client = MagicMock()
    mock_cursor_key_exchange(mock_client, email=email)
    mock_client.exchange_api_key.return_value = "session-token"
    mock_client.get_access_token.return_value = "session-token"
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    from pulse.integrations.cursor_api import map_usage_event

    mock_client.iter_filtered_usage_events.return_value = iter([map_usage_event(raw_event)])
    return mock_client


def test_create_account_requires_api_key(create_env):
    client = create_env["client"]
    token = create_access_token(create_env["config"], create_env["owner"])
    res = client.post(
        "/api/v2/accounts",
        headers=_headers(token),
        json={
            "vendor_id": create_env["cursor_vendor_id"],
            "status": "shared",
        },
    )
    assert res.status_code == 400
    assert "API Key" in res.json()["detail"]


def test_create_account_rejects_non_cursor_vendor(create_env):
    client = create_env["client"]
    token = create_access_token(create_env["config"], create_env["owner"])
    res = client.post(
        "/api/v2/accounts",
        headers=_headers(token),
        json={
            "vendor_id": create_env["other_vendor_id"],
            "status": "shared",
            "api_key": "crsr_test_api_key_abcdefghijklmnop",
        },
    )
    assert res.status_code == 400
    assert "Cursor" in res.json()["detail"]


def test_create_account_rejects_available_status(create_env):
    client = create_env["client"]
    token = create_access_token(create_env["config"], create_env["owner"])
    res = client.post(
        "/api/v2/accounts",
        headers=_headers(token),
        json={
            "vendor_id": create_env["cursor_vendor_id"],
            "status": "available",
            "api_key": "crsr_test_api_key_abcdefghijklmnop",
        },
    )
    assert res.status_code == 400
    assert "类型无效" in res.json()["detail"]


@patch("pulse.web.accounts_api.CursorApiClient")
def test_create_account_autofills_from_key(mock_client_cls, create_env):
    client = create_env["client"]
    token = create_access_token(create_env["config"], create_env["owner"])
    mock_client_cls.return_value = _mock_cursor_client(email="autofill@test.com")

    res = client.post(
        "/api/v2/accounts",
        headers=_headers(token),
        json={
            "vendor_id": create_env["cursor_vendor_id"],
            "status": "shared",
            "api_key": "crsr_test_api_key_abcdefghijklmnop",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["account_identifier"] == "autofill@test.com"
    assert body["plan_name"] == "Pro+"  # fixture limit 7000 cents
    assert body["usage_resets_on"] == "2026-07-25"
    assert body["resets_on_source"] == "api"

    status = client.get(
        f"/api/v2/accounts/{body['id']}/credentials",
        headers=_headers(token),
    )
    assert status.status_code == 200
    assert status.json()["bound"] is True
