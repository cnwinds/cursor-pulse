from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.storage.models import Base, Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def cred_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")
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

    client = TestClient(create_app(config, sf))
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


@patch("pulse.ingestion.sync.CursorApiClient")
@patch("pulse.ingestion.credentials.CursorApiClient")
def test_bind_credential_triggers_sync(mock_cred_client_cls, mock_sync_client_cls, cred_env):
    client = cred_env["client"]
    config = cred_env["config"]
    member = cred_env["member"]
    account = cred_env["cursor_account"]
    token = create_access_token(config, member)

    mock_client = MagicMock()
    mock_client.exchange_api_key.return_value = "session-token"
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    mock_client.iter_filtered_usage_events.return_value = iter([])
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


def test_bind_credential_forbidden_for_non_primary(cred_env):
    client = cred_env["client"]
    config = cred_env["config"]
    account = cred_env["cursor_account"]
    sf = cred_env["session_factory"]

    session = sf()
    other = Member(
        team_id=cred_env["team_id"],
        dingtalk_user_id="stranger",
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
