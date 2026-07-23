from __future__ import annotations

def _msg(result):
    data = result.result or {}
    return result.user_message or data.get("text") or data.get("answer") or ""


import base64
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.invoke import invoke_capability
from pulse.config import AppConfig, CredentialConfig, LoanSelectionConfig, TenantConfig
from pulse.ingestion.credentials import CredentialService
from pulse.storage.db import init_db
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.key_loans import KeyLoanError, KeyLoanService, issue_loan_key, request_self_service_loan
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def loan_bot_env():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    borrower = repo.add_member("borrower-dt", "Borrower")
    admin = repo.add_member("admin-dt", "Admin")
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    own_account = accounts[0]
    lender_account = accounts[1]
    tool_repo.update_account(own_account.id, primary_member_id=borrower.id, status="shared")
    session.flush()

    exhausted_snap = AccountQuotaSnapshot(
        account_id=own_account.id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        limit_cents=7000,
        used_cents=7000,
        remaining_cents=0,
        total_pct=100.0,
    )
    healthy_snap = AccountQuotaSnapshot(
        account_id=lender_account.id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        limit_cents=7000,
        used_cents=1000,
        remaining_cents=6000,
        total_pct=14.0,
    )
    session.add(exhausted_snap)
    session.add(healthy_snap)
    repo.commit()
    yield {
        "repo": repo,
        "borrower": borrower,
        "admin": admin,
        "own_account": own_account,
        "lender_account": lender_account,
        "session_factory": session_factory,
    }
    session.close()


def _config() -> AppConfig:
    return AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )


def _bind_borrower_own_key(session, env, mock_client) -> None:
    mock_cursor_key_exchange(mock_client, email=env["own_account"].account_identifier.lower())
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=env["own_account"].id,
        api_key="crsr_own_key_for_borrower_test_abcdefghijklmnop",
        member_id=env["borrower"].id,
    )


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_request_self_service_loan_success(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_self_service_key_value"}
    mock_client.list_user_api_keys.return_value = [{"id": 42, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    result = request_self_service_loan(
        session,
        TEST_KEY,
        team_id=env["repo"].team_id,
        borrower=env["borrower"],
        note="项目赶工",
        cursor_client=mock_client,
    )
    assert result["api_key"].startswith("pka_")
    assert result["delivery_mode"] == "proxy_alias"
    assert result["source_account_identifier"] == env["lender_account"].account_identifier

    loan_svc = KeyLoanService(session, TEST_KEY, cursor_client=mock_client)
    active = loan_svc.active_loan_for_borrower(env["borrower"].id)
    assert active is not None


def test_request_self_service_loan_rejects_missing_borrower_key(loan_bot_env):
    env = loan_bot_env
    session = env["repo"].session

    with pytest.raises(KeyLoanError, match="未绑 Key"):
        request_self_service_loan(
            session,
            TEST_KEY,
            team_id=env["repo"].team_id,
            borrower=env["borrower"],
        )


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_request_self_service_loan_rejects_partially_bound_accounts(
    mock_client_cls, loan_bot_env
):
    env = loan_bot_env
    session = env["repo"].session
    mock_client = MagicMock()
    _bind_borrower_own_key(session, env, mock_client)

    second = env["lender_account"]
    tool_repo = ToolCenterRepository(session, env["repo"].team_id)
    tool_repo.update_account(second.id, primary_member_id=env["borrower"].id, status="shared")
    session.flush()

    with pytest.raises(KeyLoanError, match="未绑 Key") as exc:
        request_self_service_loan(
            session,
            TEST_KEY,
            team_id=env["repo"].team_id,
            borrower=env["borrower"],
            cursor_client=mock_client,
        )
    assert "1 个 Cursor 账号未绑 Key" in str(exc.value)


def test_request_self_service_loan_rejects_healthy_account(loan_bot_env):
    env = loan_bot_env
    session = env["repo"].session
    mock_client = MagicMock()
    _bind_borrower_own_key(session, env, mock_client)
    own_snap = session.scalar(
        select(AccountQuotaSnapshot).where(AccountQuotaSnapshot.account_id == env["own_account"].id)
    )
    own_snap.total_pct = 20.0
    own_snap.used_cents = 1400
    own_snap.remaining_cents = 5600
    session.flush()

    with pytest.raises(KeyLoanError, match="额度尚充足"):
        request_self_service_loan(
            session,
            TEST_KEY,
            team_id=env["repo"].team_id,
            borrower=env["borrower"],
        )


def _invoke(session, *, team_id: str, member_id: str, capability_key: str, arguments: dict | None = None, config=None):
    return invoke_capability(
        session,
        request=CapabilityInvokeRequest(
            invocation_id=f"inv-{capability_key}",
            idempotency_key=f"idem-{capability_key}",
            capability_key=capability_key,
            capability_version="1",
            team_id=team_id,
            actor_member_id=member_id,
            arguments=arguments or {},
            confirmed_by=member_id,
        ),
        config=config or _config(),
    )


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_dingtalk_borrow_key_command(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    config = _config()
    config.admin.dingtalk_user_ids = [env["admin"].dingtalk_user_id]

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_bot_loan_key_plaintext"}
    mock_client.list_user_api_keys.return_value = [{"id": 7, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    result = _invoke(
        session,
        team_id=env["repo"].team_id,
        member_id=env["borrower"].id,
        capability_key="key.loan.request",
        arguments={"text": "借 Key 项目赶工"},
        config=config,
    )
    reply = _msg(result)
    api_key = (result.result or {}).get("api_key") or ""
    assert api_key.startswith("pka_")
    assert (result.result or {}).get("delivery_mode") == "proxy_alias"
    assert result.user_message == ""
    assert (result.result or {}).get("schema_version") == 1
    assert "loan_expires_on" in (result.result or {})


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_dingtalk_borrow_key_natural_language(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    config = _config()
    config.admin.dingtalk_user_ids = [env["admin"].dingtalk_user_id]

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_bot_loan_key_plaintext"}
    mock_client.list_user_api_keys.return_value = [{"id": 7, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    result = _invoke(
        session,
        team_id=env["repo"].team_id,
        member_id=env["borrower"].id,
        capability_key="key.loan.request",
        arguments={"note": "用量不够，申请临时 key"},
        config=config,
    )
    reply = _msg(result)
    api_key = (result.result or {}).get("api_key") or ""
    assert api_key.startswith("pka_")
    assert (result.result or {}).get("delivery_mode") == "proxy_alias"
    assert result.user_message == ""


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_dingtalk_self_loan_read_includes_key(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    config = _config()
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_bot_loan_key_plaintext"}
    mock_client.list_user_api_keys.return_value = [{"id": 7, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    tool_repo = ToolCenterRepository(session, env["repo"].team_id)
    tool_repo.update_account(env["lender_account"].id, primary_member_id=env["admin"].id)
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    request_self_service_loan(
        session,
        TEST_KEY,
        team_id=env["repo"].team_id,
        borrower=env["borrower"],
        cursor_client=mock_client,
    )
    session.flush()

    result = _invoke(
        session,
        team_id=env["repo"].team_id,
        member_id=env["borrower"].id,
        capability_key="key.loan.self.read",
        config=config,
    )
    assert _msg(result) == ""
    payload = result.result or {}
    assert payload.get("schema_version") == 1
    loans = payload.get("loans") or []
    assert len(loans) >= 1
    loan = loans[0]
    assert loan.get("lender_name") == "Admin"
    assert loan.get("usage_source") in {"proxy", "quota_approx"}
    assert "proxy_request_count" in loan
    assert "proxy_total_tokens" in loan
    assert "proxy_cost_usd" in loan
    assert str(loan.get("api_key") or "").startswith("pka_")
    assert loan.get("delivery_mode") == "proxy_alias"
    assert loan.get("requires_proxy") is True
    assert payload.get("empty_reason") is None
    assert str(payload.get("loan", {}).get("api_key") or "").startswith("pka_")
@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_dingtalk_return_key_command(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    config = _config()
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_bot_loan_key_plaintext"}
    mock_client.list_user_api_keys.return_value = [{"id": 7, "name": "pulse-loan-Borrower"}]
    mock_client.revoke_user_api_key.return_value = None
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    request_self_service_loan(
        session,
        TEST_KEY,
        team_id=env["repo"].team_id,
        borrower=env["borrower"],
        cursor_client=mock_client,
    )
    session.flush()

    result = _invoke(
        session,
        team_id=env["repo"].team_id,
        member_id=env["borrower"].id,
        capability_key="key.loan.return",
        config=config,
    )
    reply = _msg(result)
    assert "已归还借用" in reply

    loan_svc = KeyLoanService(session, TEST_KEY, cursor_client=mock_client)
    assert loan_svc.active_loan_for_borrower(env["borrower"].id) is None


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_self_service_recommend_respects_configured_cap(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_cap_thread_key"}
    mock_client.list_user_api_keys.return_value = [{"id": 43, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    # 先占掉出借账号在 cap=1 下的唯一名额
    occupant = env["repo"].add_member("cap-occupant", "CapOccupant")
    session.flush()
    issue_loan_key(
        session,
        TEST_KEY,
        team_id=env["repo"].team_id,
        source_account_id=env["lender_account"].id,
        borrower_member_id=occupant.id,
        bound_by_member_id=env["admin"].id,
        cursor_client=mock_client,
    )

    with pytest.raises(KeyLoanError, match="没有可借出"):
        request_self_service_loan(
            session,
            TEST_KEY,
            team_id=env["repo"].team_id,
            borrower=env["borrower"],
            loan_selection=LoanSelectionConfig(max_active_loans_per_account=1),
        )


@patch("pulse.tool_center.key_loans.CursorApiClient")
def test_self_service_rejects_second_active_loan(mock_client_cls, loan_bot_env):
    env = loan_bot_env
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_second_loan_key"}
    mock_client.list_user_api_keys.return_value = [{"id": 44, "name": "pulse-loan-Borrower"}]
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())

    session = env["repo"].session
    _bind_borrower_own_key(session, env, mock_client)
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=env["lender_account"].account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=env["lender_account"].id,
        api_key="crsr_primary_key_for_lender_test_abcdefghij",
        member_id=env["admin"].id,
    )
    session.flush()

    request_self_service_loan(
        session,
        TEST_KEY,
        team_id=env["repo"].team_id,
        borrower=env["borrower"],
        cursor_client=mock_client,
    )
    with pytest.raises(KeyLoanError, match="已有进行中的借用"):
        request_self_service_loan(
            session,
            TEST_KEY,
            team_id=env["repo"].team_id,
            borrower=env["borrower"],
            cursor_client=mock_client,
        )
