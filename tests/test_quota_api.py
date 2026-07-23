from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.ingestion.credentials import CredentialService
from pulse.storage.models import AccountQuotaSnapshot, Base, Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def quota_env():
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
    borrower = repo.add_member("borrower", "Borrower")
    borrower.portal_role = "ai_member"
    borrower.portal_status = "active"
    seed_v2_catalog(s, team)
    s.flush()

    tool_repo = ToolCenterRepository(s, team.id)
    cursor_account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(cursor_account.id, primary_member_id=borrower.id, status="shared")

    snap = AccountQuotaSnapshot(
        account_id=cursor_account.id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        limit_cents=7000,
        used_cents=2000,
        remaining_cents=5000,
        total_pct=28.5,
    )
    s.add(snap)
    repo.commit()
    s.close()

    client = TestClient(create_app(config, sf))
    return {
        "client": client,
        "config": config,
        "owner": owner,
        "borrower": borrower,
        "cursor_account": cursor_account,
        "session_factory": sf,
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_quota_board_lists_cursor_accounts(quota_env):
    client = quota_env["client"]
    token = create_access_token(quota_env["config"], quota_env["owner"])
    res = client.get("/api/v2/quota-board", headers=_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert any(item["has_snapshot"] for item in data)
    matched = next(item for item in data if item["account_id"] == quota_env["cursor_account"].id)
    assert matched["primary_member_name"] == quota_env["borrower"].display_name


def test_loan_key_returns_plaintext_once(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_key_plaintext_value"}
    mock_client.list_user_api_keys.return_value = [{"id": 99, "name": "pulse-loan-Borrower"}]
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())

    with patch("pulse.tool_center.key_loans.CursorApiClient", return_value=mock_client):
        s = quota_env["session_factory"]()
        cred_service = CredentialService(s, TEST_KEY, cursor_client=mock_client)
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_primary_key_for_loan_test_abcdefghij",
            member_id=owner.id,
        )
        s.commit()
        s.close()

        res = client.post(
            f"/api/v2/accounts/{account.id}/loan-key",
            headers=_headers(token),
            json={
                "borrower_member_id": borrower.id,
                "auto_revoke_on_reset": True,
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert "api_key" in body
    assert body["api_key"].startswith("pka_")
    assert body["delivery_mode"] == "proxy_alias"


def test_loan_key_rejects_invalid_delivery_mode(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    res = client.post(
        f"/api/v2/accounts/{account.id}/loan-key",
        headers=_headers(token),
        json={
            "borrower_member_id": borrower.id,
            "delivery_mode": "not_a_real_mode",
        },
    )
    assert res.status_code == 422


def test_loan_key_cursor_direct_mode(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_key_plaintext_value"}
    mock_client.list_user_api_keys.return_value = [{"id": 99, "name": "pulse-loan-Borrower"}]
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())

    with patch("pulse.tool_center.key_loans.CursorApiClient", return_value=mock_client):
        s = quota_env["session_factory"]()
        cred_service = CredentialService(s, TEST_KEY, cursor_client=mock_client)
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_primary_key_for_loan_test_abcdefghij",
            member_id=owner.id,
        )
        s.commit()
        s.close()

        res = client.post(
            f"/api/v2/accounts/{account.id}/loan-key",
            headers=_headers(token),
            json={
                "borrower_member_id": borrower.id,
                "auto_revoke_on_reset": True,
                "delivery_mode": "cursor_direct",
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body["api_key"].startswith("crsr_")
    assert body["delivery_mode"] == "cursor_direct"


def test_loan_key_rejects_borrower_without_cursor_key(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    session = quota_env["session_factory"]()
    tool_repo = ToolCenterRepository(session, quota_env["owner"].team_id)
    accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    borrower_account = next(a for a in accounts if a.id != account.id)
    tool_repo.update_account(borrower_account.id, primary_member_id=borrower.id, status="shared")
    session.commit()
    session.close()

    res = client.post(
        f"/api/v2/accounts/{account.id}/loan-key",
        headers=_headers(token),
        json={
            "borrower_member_id": borrower.id,
            "auto_revoke_on_reset": True,
        },
    )
    assert res.status_code == 400
    assert "未绑 Key" in res.json()["detail"]


def test_list_loans_includes_primary_member_name(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_key_plaintext_value"}
    mock_client.list_user_api_keys.return_value = [{"id": 99, "name": "pulse-loan-Borrower"}]
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())

    with patch("pulse.tool_center.key_loans.CursorApiClient", return_value=mock_client):
        s = quota_env["session_factory"]()
        cred_service = CredentialService(s, TEST_KEY, cursor_client=mock_client)
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_primary_key_for_loan_test_abcdefghij",
            member_id=owner.id,
        )
        s.commit()
        s.close()

        client.post(
            f"/api/v2/accounts/{account.id}/loan-key",
            headers=_headers(token),
            json={
                "borrower_member_id": borrower.id,
                "auto_revoke_on_reset": True,
            },
        )

    res = client.get("/api/v2/loans", headers=_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["active_count"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    loan = body["items"][0]
    assert loan["primary_member_name"] == borrower.display_name
    assert loan["status"] == "active"
    assert loan["borrowed_cents"] == 0

    active_only = client.get("/api/v2/loans", headers=_headers(token), params={"status": "active"})
    assert active_only.status_code == 200
    assert active_only.json()["total"] == 1
    assert len(active_only.json()["items"]) == 1


def test_revoke_loan_idempotent_payload(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    token = create_access_token(config, owner)

    res = client.get("/api/v2/loans", headers=_headers(token))
    assert res.status_code == 200
    assert "items" in res.json()
    assert "active_count" in res.json()


def test_quota_recommend_returns_lender_ranking(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    token = create_access_token(config, owner)

    s = quota_env["session_factory"]()
    snap = s.scalar(
        select(AccountQuotaSnapshot).where(
            AccountQuotaSnapshot.account_id == account.id
        )
    )
    snap.cycle_start = date.today() - timedelta(days=15)
    snap.cycle_end = date.today() + timedelta(days=15)
    s.commit()
    s.close()

    res = client.get("/api/v2/quota-board/recommend", headers=_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    item = data[0]
    assert item["account_id"] == account.id
    assert "deadline" in item
    assert "surplus_cents" in item
    assert "active_loans" in item


def test_loan_key_enforces_account_cap(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_key_plaintext_value"}
    mock_client.list_user_api_keys.return_value = [{"id": 99, "name": "pulse-loan-Borrower"}]
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())

    with patch("pulse.tool_center.key_loans.CursorApiClient", return_value=mock_client):
        s = quota_env["session_factory"]()
        cred_service = CredentialService(s, TEST_KEY, cursor_client=mock_client)
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_primary_key_for_loan_test_abcdefghij",
            member_id=owner.id,
        )
        s.commit()
        s.close()

        url = f"/api/v2/accounts/{account.id}/loan-key"
        for _ in range(2):
            res = client.post(
                url,
                headers=_headers(token),
                json={"borrower_member_id": borrower.id, "auto_revoke_on_reset": True},
            )
            assert res.status_code == 200

        res = client.post(
            url,
            headers=_headers(token),
            json={"borrower_member_id": borrower.id, "auto_revoke_on_reset": True},
        )
        assert res.status_code == 400
        assert "名额已满" in res.json()["detail"]
