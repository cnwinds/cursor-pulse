from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.storage.models import Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture(scope="module")
def _cred_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def cred_env(_cred_app):
    client, config, proxy = _cred_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    member = repo.add_member("u1", "Alice")
    member.portal_role = "ai_member"
    member.portal_status = "active"
    seed_v2_catalog(s, team)
    s.flush()

    tool_repo = ToolCenterRepository(s, team.id)
    cursor_account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(cursor_account.id, primary_member_id=member.id, status="trial")
    repo.commit()
    s.close()

    return {
        "client": client,
        "config": config,
        "owner": owner,
        "member": member,
        "cursor_account": cursor_account,
        "session_factory": sf,
        "team_id": team.id,
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_credential_status_unbound(cred_env):
    client = cred_env["client"]
    config = cred_env["config"]
    owner = cred_env["owner"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, owner)

    res = client.get(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["bound"] is False
    assert body["last_sync_status"] == "never"


def test_list_accounts_embeds_credential_status_in_one_response(cred_env):
    """Accounts list must carry credential summary so the UI need not N+1 GET /credentials."""
    from datetime import datetime, timezone

    from pulse.storage.models import AiAccountCredential

    client = cred_env["client"]
    config = cred_env["config"]
    owner = cred_env["owner"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, owner)
    sf = cred_env["session_factory"]

    session = sf()
    from pulse.storage.models import AiAccount

    account_row = session.get(AiAccount, account.id)
    assert account_row is not None
    session.add(
        AiAccountCredential(
            account_id=account.id,
            vendor_id=account_row.vendor_id,
            credential_type="api_key",
            encrypted_value="unused",
            key_hint="crsr_…abcd",
            status="active",
            sync_enabled=True,
            bound_by_member_id=owner.id,
            last_sync_status="success",
            last_sync_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
    )
    session.commit()
    session.close()

    res = client.get("/api/v2/accounts", headers=_headers(token))
    assert res.status_code == 200
    rows = res.json()
    cursor_row = next(r for r in rows if r["id"] == account.id)
    assert "credential" in cursor_row
    assert cursor_row["credential"]["bound"] is True
    assert cursor_row["credential"]["key_hint"] == "crsr_…abcd"
    assert cursor_row["credential"]["last_sync_status"] == "success"
    assert "encrypted_value" not in cursor_row["credential"]


@patch("pulse.ingestion.sync.CursorApiClient")
@patch("pulse.ingestion.credentials.CursorApiClient")
def test_bind_credential_triggers_sync(mock_cred_client_cls, mock_sync_client_cls, cred_env):
    client = cred_env["client"]
    config = cred_env["config"]
    member = cred_env["member"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, member)

    mock_client = MagicMock()
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())
    mock_client.exchange_api_key.return_value = "session-token"
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    from pulse.integrations.cursor_api import map_usage_event

    mock_client.iter_filtered_usage_events.return_value = iter([map_usage_event(raw_event)])
    mock_cred_client_cls.return_value = mock_client
    mock_sync_client_cls.return_value = mock_client

    res = client.post(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
        json={"api_key": "crsr_test_api_key_abcdefghijklmnop"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["bound"] is True
    assert body["key_hint"].startswith("crsr_")
    assert "encrypted_value" not in body
    assert body["last_sync_status"] == "success"
    assert body["sync"]["event_count"] == 1


@patch("pulse.ingestion.sync.CursorApiClient")
@patch("pulse.ingestion.credentials.CursorApiClient")
def test_bind_credential_account_mismatch_returns_409(
    mock_cred_client_cls, mock_sync_client_cls, cred_env
):
    client = cred_env["client"]
    config = cred_env["config"]
    member = cred_env["member"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, member)

    mock_client = MagicMock()
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email="other-user@example.com")
    mock_cred_client_cls.return_value = mock_client
    mock_sync_client_cls.return_value = mock_client

    res = client.post(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
        json={"api_key": "crsr_test_api_key_abcdefghijklmnop"},
    )
    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["code"] == "account_email_mismatch"
    assert body["key_email"] == "other-user@example.com"
    assert body["ledger_email"] == account.account_identifier


@patch("pulse.ingestion.sync.CursorApiClient")
@patch("pulse.ingestion.credentials.CursorApiClient")
def test_bind_credential_auto_fills_empty_identifier(
    mock_cred_client_cls, mock_sync_client_cls, cred_env
):
    client = cred_env["client"]
    config = cred_env["config"]
    member = cred_env["member"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, member)
    key_email = "auto-filled@example.com"
    sf = cred_env["session_factory"]

    session = sf()
    from pulse.storage.models import AiAccount

    row = session.get(AiAccount, account.id)
    row.account_identifier = ""
    session.commit()
    session.close()

    mock_client = MagicMock()
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=key_email)
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    from pulse.integrations.cursor_api import map_usage_event

    mock_client.iter_filtered_usage_events.return_value = iter([map_usage_event(raw_event)])
    mock_cred_client_cls.return_value = mock_client
    mock_sync_client_cls.return_value = mock_client

    res = client.post(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
        json={"api_key": "crsr_test_api_key_abcdefghijklmnop"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["bound"] is True
    assert body["account_identifier"] == key_email

    session = sf()
    updated = session.get(AiAccount, account.id)
    assert updated.account_identifier == key_email
    session.close()


def test_bind_credential_forbidden_for_non_primary(cred_env):
    client = cred_env["client"]
    config = cred_env["config"]
    account = cred_env["cursor_account"]
    sf = cred_env["session_factory"]

    session = sf()
    other = Member(
        team_id=cred_env["team_id"],
        channel_user_id="stranger",
        display_name="Stranger",
        status="active",
        portal_status="active",
        portal_role="ai_member",
    )
    session.add(other)
    session.commit()
    session.close()

    token = create_access_token(config, other)
    res = client.post(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
        json={"api_key": "crsr_test_api_key_abcdefghijklmnop"},
    )
    assert res.status_code == 403


def test_revoke_credential(cred_env):
    client = cred_env["client"]
    config = cred_env["config"]
    member = cred_env["member"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, member)

    with patch("pulse.ingestion.credentials.CursorApiClient") as mock_cls:
        from tests.conftest import mock_cursor_key_exchange

        mock_cursor_key_exchange(mock_cls.return_value, email=account.account_identifier.lower())
        mock_cls.return_value.exchange_api_key.return_value = "token"
        client.post(
            f"/api/v2/accounts/{account.id}/credentials",
            headers=_headers(token),
            json={"api_key": "crsr_test_api_key_abcdefghijklmnop"},
        )

    res = client.delete(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
    )
    assert res.status_code == 200

    status = client.get(
        f"/api/v2/accounts/{account.id}/credentials",
        headers=_headers(token),
    ).json()
    assert status["bound"] is False
