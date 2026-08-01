from __future__ import annotations

import base64
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, ProxyConfig, TenantConfig, WebConfig
from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.crypto import encrypt_secret
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    AiAccountCredential,
    KeyLoan,
    ProxyKeyUsage,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import (
    make_module_web_client,
    make_team_repo,
    make_test_session_factory,
    mock_cursor_key_exchange,
)

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
LOAN_PLAINTEXT = "crsr_loan_client_setup_key_plaintext"


@pytest.fixture(scope="module")
def _loan_client_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        proxy=ProxyConfig(public_url="http://proxy.example.com:8317"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def loan_client_env(_loan_client_app):
    client, config, proxy = _loan_client_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(
        repo, channel_user_id="admin", display_name="Admin", password="x"
    )
    borrower = repo.add_member("borrower", "Borrower")
    borrower.portal_role = "ai_member"
    borrower.portal_status = "active"
    seed_v2_catalog(s, team)
    s.flush()

    tool_repo = ToolCenterRepository(s, team.id)
    cursor_account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(cursor_account.id, primary_member_id=borrower.id, status="shared")
    s.add(
        AccountQuotaSnapshot(
            account_id=cursor_account.id,
            captured_at=datetime.now(timezone.utc),
            cycle_start=date(2026, 7, 1),
            cycle_end=date(2026, 8, 1),
            limit_cents=7000,
            used_cents=2000,
            remaining_cents=5000,
            total_pct=28.5,
        )
    )
    repo.commit()
    s.close()

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


def _issue_loan(env) -> tuple[str, str]:
    """Return (loan_id, api_key) after issuing a loan via API."""
    client = env["client"]
    config = env["config"]
    owner = env["owner"]
    account = env["cursor_account"]
    borrower = env["borrower"]
    token = create_access_token(config, owner)

    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": LOAN_PLAINTEXT}
    mock_client.list_user_api_keys.return_value = [{"id": 99, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())

    with patch("pulse.tool_center.key_loan_store.CursorApiClient", return_value=mock_client):
        s = env["session_factory"]()
        cred_service = CredentialService(s, TEST_KEY, cursor_client=mock_client)
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_primary_key_for_loan_client_setup_test",
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
    return body["loan_id"], body["api_key"]


def test_loan_payload_includes_proxy_cost(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    s.add(
        ProxyKeyUsage(
            proxy_key_id=None,
            loan_id=loan_id,
            credential_id="cred-1",
            total_tokens=100,
            cost_cents=42,
        )
    )
    s.add(
        ProxyKeyUsage(
            proxy_key_id=None,
            loan_id=loan_id,
            credential_id="cred-1",
            total_tokens=50,
            cost_cents=8,
        )
    )
    s.commit()
    s.close()

    res = env["client"].get("/api/v2/loans", headers=_headers(token))
    assert res.status_code == 200
    loan = next(item for item in res.json()["items"] if item["id"] == loan_id)
    assert loan["proxy_cost_cents"] == 50


def test_loan_usages_detail(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    loan = s.get(KeyLoan, loan_id)
    assert loan is not None
    s.add(
        ProxyKeyUsage(
            proxy_key_id=None,
            loan_id=loan_id,
            credential_id=loan.credential_id,
            model="gpt-5",
            total_tokens=100,
            cost_cents=42,
        )
    )
    s.commit()
    account_identifier = env["cursor_account"].account_identifier
    s.close()

    res = env["client"].get(f"/api/v2/loans/{loan_id}/usages", headers=_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["proxy_cost_cents"] == 42
    assert body["summary"]["proxy_total_tokens"] == 100
    assert body["summary"]["request_count"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["model"] == "gpt-5"
    assert body["items"][0]["account_identifier"] == account_identifier
    assert body["items"][0]["primary_member_name"] == "Borrower"
    assert len(body["by_account"]) == 1
    assert body["by_account"][0]["account_id"] == env["cursor_account"].id
    assert body["by_account"][0]["account_identifier"] == account_identifier
    assert body["by_account"][0]["primary_member_name"] == "Borrower"
    assert body["by_account"][0]["request_count"] == 1
    assert body["by_account"][0]["total_tokens"] == 100
    assert body["by_account"][0]["cost_cents"] == 42
    assert body["by_model"] == [
        {"model": "gpt-5", "request_count": 1, "total_tokens": 100, "cost_cents": 42}
    ]
    assert len(body["by_day"]) == 1
    assert body["by_day"][0]["request_count"] == 1
    assert body["by_day"][0]["total_tokens"] == 100
    assert body["by_day"][0]["cost_cents"] == 42
    assert len(body["by_day"][0]["items"]) == 1
    assert body["by_day"][0]["items"][0]["model"] == "gpt-5"
    assert body["by_day"][0]["items"][0]["account_identifier"] == account_identifier


def test_loan_usages_grouped_by_account(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    primary = s.get(AiAccount, env["cursor_account"].id)
    assert primary is not None
    other = AiAccount(
        team_id=primary.team_id,
        vendor_id=primary.vendor_id,
        plan_id=primary.plan_id,
        account_identifier="other-lender@example.com",
        status="shared",
        primary_member_id=env["owner"].id,
        ownership="company",
    )
    s.add(other)
    s.flush()
    other_cred = AiAccountCredential(
        account_id=other.id,
        vendor_id=primary.vendor_id,
        credential_type="cursor_api_key",
        encrypted_value="enc-other",
        key_hint="****other",
        key_role="primary",
        bound_by_member_id=env["owner"].id,
    )
    s.add(other_cred)
    s.flush()
    loan = s.get(KeyLoan, loan_id)
    assert loan is not None
    primary_cred_id = loan.credential_id
    s.add_all(
        [
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id=loan_id,
                credential_id=primary_cred_id,
                model="claude-a",
                total_tokens=100,
                cost_cents=10,
            ),
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id=loan_id,
                credential_id=primary_cred_id,
                model="claude-b",
                total_tokens=50,
                cost_cents=5,
            ),
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id=loan_id,
                credential_id=other_cred.id,
                model="gpt-5",
                total_tokens=80,
                cost_cents=8,
            ),
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id=loan_id,
                credential_id=None,
                model="unknown",
                total_tokens=7,
                cost_cents=1,
            ),
        ]
    )
    s.commit()
    primary_id = primary.account_identifier
    s.close()

    res = env["client"].get(f"/api/v2/loans/{loan_id}/usages", headers=_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert len(body["by_account"]) == 3
    top = body["by_account"][0]
    assert top["account_identifier"] == primary_id
    assert top["primary_member_name"] == "Borrower"
    assert top["request_count"] == 2
    assert top["total_tokens"] == 150
    assert top["cost_cents"] == 15
    assert body["by_account"][1]["account_identifier"] == "other-lender@example.com"
    assert body["by_account"][1]["primary_member_name"] == "Admin"
    assert body["by_account"][1]["request_count"] == 1
    assert body["by_account"][1]["total_tokens"] == 80
    assert body["by_account"][2]["account_identifier"] == "未知账号"
    assert body["by_account"][2]["primary_member_name"] is None
    matched = next(i for i in body["items"] if i["account_identifier"] == primary_id)
    assert matched["primary_member_name"] == "Borrower"
    assert any(
        i["account_identifier"] is None and i["primary_member_name"] is None for i in body["items"]
    )


def test_loan_usages_by_model_aggregates_all_rows(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    for model, tokens, cost in [
        ("claude-opus-4-8", 1000, 80),
        ("claude-opus-4-8", 500, 40),
        ("composer-2.5-fast", 200, 10),
        (None, 50, 5),
        ("", 25, 2),
    ]:
        s.add(
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id=loan_id,
                credential_id="cred-1",
                model=model,
                total_tokens=tokens,
                cost_cents=cost,
            )
        )
    s.commit()
    s.close()

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/usages",
        params={"limit": 2},
        headers=_headers(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["request_count"] == 5
    assert len(body["items"]) == 2
    assert body["by_model"] == [
        {
            "model": "claude-opus-4-8",
            "request_count": 2,
            "total_tokens": 1500,
            "cost_cents": 120,
        },
        {
            "model": "composer-2.5-fast",
            "request_count": 1,
            "total_tokens": 200,
            "cost_cents": 10,
        },
        {
            "model": "（未知）",
            "request_count": 2,
            "total_tokens": 75,
            "cost_cents": 7,
        },
    ]


def test_loan_usages_by_day_groups_china_calendar(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    # 2026-07-23 16:00 UTC = 2026-07-24 00:00 China
    # 2026-07-23 15:00 UTC = 2026-07-23 23:00 China
    for ts, tokens, cost in [
        (datetime(2026, 7, 23, 16, 0, 0, tzinfo=timezone.utc), 100, 10),
        (datetime(2026, 7, 23, 17, 0, 0, tzinfo=timezone.utc), 200, 20),
        (datetime(2026, 7, 23, 15, 0, 0, tzinfo=timezone.utc), 50, 5),
    ]:
        s.add(
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id=loan_id,
                credential_id="cred-1",
                model="gpt-5",
                total_tokens=tokens,
                cost_cents=cost,
                ts=ts,
            )
        )
    s.commit()
    s.close()

    res = env["client"].get(f"/api/v2/loans/{loan_id}/usages", headers=_headers(token))
    assert res.status_code == 200
    by_day = res.json()["by_day"]
    assert [d["day"] for d in by_day] == ["2026-07-24", "2026-07-23"]
    assert by_day[0] == {
        "day": "2026-07-24",
        "request_count": 2,
        "total_tokens": 300,
        "cost_cents": 30,
        "items": by_day[0]["items"],
    }
    assert len(by_day[0]["items"]) == 2
    assert by_day[1]["request_count"] == 1
    assert by_day[1]["total_tokens"] == 50
    assert by_day[1]["cost_cents"] == 5


def test_loan_client_setup_powershell(loan_client_env):
    env = loan_client_env
    loan_id, api_key = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        params={"shell": "powershell"},
        headers=_headers(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["plaintext_key"] == api_key
    assert api_key.startswith("pka_")
    assert body["delivery_mode"] == "proxy_alias"
    assert body["proxy_url"] == "http://proxy.example.com:8317"
    assert body["shell"] == "powershell"
    assert "$env:HTTPS_PROXY" in body["command"]
    assert "$env:CURSOR_API_KEY" in body["command"]
    assert api_key in body["command"]
    assert "agent -k" in body["command"]


def test_loan_client_setup_bash(loan_client_env):
    env = loan_client_env
    loan_id, api_key = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        params={"shell": "bash"},
        headers=_headers(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert "export HTTPS_PROXY" in body["command"]
    assert "export CURSOR_API_KEY" in body["command"]
    assert api_key in body["command"]
    assert "agent -k" in body["command"]


def test_loan_client_setup_revoked_410(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    loan = s.get(KeyLoan, loan_id)
    loan.status = "revoked"
    s.commit()
    s.close()

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        headers=_headers(token),
    )
    assert res.status_code == 410


def test_loan_client_setup_missing_404(loan_client_env):
    env = loan_client_env
    token = create_access_token(env["config"], env["owner"])
    res = env["client"].get(
        "/api/v2/loans/nonexistent-loan-id/client-setup",
        headers=_headers(token),
    )
    assert res.status_code == 404


def test_loan_client_setup_undecryptable_410(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    loan = s.get(KeyLoan, loan_id)
    # proxy_alias 默认：清空别名密文应导致 client-setup 410
    loan.alias_encrypted_key = ""
    s.commit()
    s.close()

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        headers=_headers(token),
    )
    assert res.status_code == 410


def test_loan_client_setup_allows_borrower_self(loan_client_env):
    """Borrowers with loans:self may fetch client-setup for their own active loan."""
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    borrower = env["borrower"]
    token = create_access_token(env["config"], borrower)

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        headers=_headers(token),
        params={"shell": "bash"},
    )
    assert res.status_code == 200
    assert "HTTPS_PROXY" in res.json()["command"]


def test_loan_client_setup_forbids_unrelated_member(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    s = env["session_factory"]()
    _, repo = make_team_repo(s)
    other = repo.add_member("other-user", "Other")
    other.portal_role = "ai_member"
    other.portal_status = "active"
    s.commit()
    other_id = other.id
    s.close()

    # Re-load member for token claims
    s = env["session_factory"]()
    from pulse.storage.models import Member

    other = s.get(Member, other_id)
    token = create_access_token(env["config"], other)
    s.close()

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        headers=_headers(token),
    )
    assert res.status_code == 403


def test_loan_cursor_key_admin_reveal(loan_client_env):
    env = loan_client_env
    loan_id, user_key = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    res = env["client"].get(
        f"/api/v2/loans/{loan_id}/cursor-key",
        headers=_headers(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["delivery_mode"] == "proxy_alias"
    assert body["cursor_api_key"] == LOAN_PLAINTEXT
    assert user_key.startswith("pka_")
    assert body["cursor_api_key"] != user_key


def test_revoke_clears_alias_fields(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    token = create_access_token(env["config"], env["owner"])

    s = env["session_factory"]()
    loan = s.get(KeyLoan, loan_id)
    assert loan.alias_key_hash
    assert loan.alias_encrypted_key
    s.close()

    with patch("pulse.tool_center.key_loan_store.CursorApiClient") as mock_cls:
        mock_cls.return_value.get_access_token.return_value = "session-token"
        mock_cls.return_value.revoke_user_api_key.return_value = None
        res = env["client"].post(
            f"/api/v2/loans/{loan_id}/revoke",
            headers=_headers(token),
        )
    assert res.status_code == 200

    s = env["session_factory"]()
    loan = s.get(KeyLoan, loan_id)
    assert loan.status == "revoked"
    assert loan.alias_key_hash is None
    assert loan.alias_key_hint is None
    assert loan.alias_encrypted_key is None
    s.close()
