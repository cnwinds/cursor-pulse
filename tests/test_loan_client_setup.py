from __future__ import annotations

import base64
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, ProxyConfig, TenantConfig, WebConfig
from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.crypto import encrypt_secret
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccountCredential,
    Base,
    KeyLoan,
    ProxyKeyUsage,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner


def make_team_repo(session, slug: str = "test"):
    from sqlalchemy import select

    from pulse.storage.models import Team

    team = session.scalar(select(Team).where(Team.slug == slug))
    if team is None:
        team = Team(slug=slug, name=slug.title())
        session.add(team)
        session.flush()
    try:
        from pulse.storage.repository import Repository

        return team, Repository(session, team.id)
    except ImportError:
        return team, None


def mock_cursor_key_exchange(mock_client, *, email: str | None = None) -> None:
    import base64
    import json
    import time

    from pulse.integrations.cursor_api import (
        _normalize_account_email,
        resolve_account_email_from_exchange,
    )

    payload: dict = {"exp": int(time.time()) + 3600}
    if email:
        payload["email"] = email
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{encoded}.sig"
    exchange = {
        "accessToken": token,
        "refreshToken": "ref",
    }
    mock_client.exchange_user_api_key_response.return_value = exchange

    def _resolve(_api_key, exchange=None):
        data = exchange or mock_client.exchange_user_api_key_response.return_value
        resolved = resolve_account_email_from_exchange(data)
        if resolved:
            return resolved
        access_token = data.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            return None
        get_me = getattr(mock_client, "get_me", None)
        if get_me is None:
            return None
        try:
            me = get_me(access_token, api_key=_api_key)
        except Exception:
            return None
        me_email = me.get("email") if isinstance(me, dict) else None
        if isinstance(me_email, str) and "@" in me_email:
            return _normalize_account_email(me_email)
        return None

    mock_client.resolve_api_key_account_email.side_effect = _resolve


TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
LOAN_PLAINTEXT = "crsr_loan_client_setup_key_plaintext"


@pytest.fixture
def loan_client_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        proxy=ProxyConfig(public_url="http://proxy.example.com:8317"),
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
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin", display_name="Admin", password="x"
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

    with patch("pulse.tool_center.key_loans.CursorApiClient", return_value=mock_client):
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
    s.add(
        ProxyKeyUsage(
            proxy_key_id=None,
            loan_id=loan_id,
            credential_id="cred-1",
            model="gpt-5",
            total_tokens=100,
            cost_cents=42,
        )
    )
    s.commit()
    s.close()

    res = env["client"].get(f"/api/v2/loans/{loan_id}/usages", headers=_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["proxy_cost_cents"] == 42
    assert body["summary"]["proxy_total_tokens"] == 100
    assert body["summary"]["request_count"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["model"] == "gpt-5"
    assert body["by_model"] == [
        {"model": "gpt-5", "request_count": 1, "total_tokens": 100, "cost_cents": 42}
    ]


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


def test_loan_client_setup_requires_accounts_write(loan_client_env):
    env = loan_client_env
    loan_id, _ = _issue_loan(env)
    borrower = env["borrower"]
    token = create_access_token(env["config"], borrower)

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

    with patch("pulse.tool_center.key_loans.CursorApiClient") as mock_cls:
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
