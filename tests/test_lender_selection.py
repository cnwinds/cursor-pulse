from __future__ import annotations

import base64
import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from pulse.config import AppConfig, CredentialConfig, TenantConfig
from pulse.ingestion.credentials import CredentialService
from pulse.storage.db import init_db
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccountCredential,
    KeyLoan,
)
from pulse.tool_center.key_loan_ops import read_self_loan
from pulse.tool_center.key_loans import (
    KeyLoanService,
    account_loan_deadline,
    build_lender_candidates,
    loan_payload,
    recommend_lender_for_borrower,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def lender_env():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    member = repo.add_member("sel-member", "SelMember")
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    today = date.today()
    for acc in accounts:
        session.add(
            AccountQuotaSnapshot(
                account_id=acc.id,
                captured_at=datetime.now(timezone.utc),
                cycle_start=today - timedelta(days=10),
                cycle_end=today + timedelta(days=20),
                limit_cents=7000,
                used_cents=1000,
                remaining_cents=6000,
                total_pct=14.0,
            )
        )
    repo.commit()
    yield {
        "session": session,
        "repo": repo,
        "tool_repo": tool_repo,
        "member": member,
        "accounts": accounts,
    }
    session.close()


def _make_loan(session, env, account, borrower) -> KeyLoan:
    cred = AiAccountCredential(
        account_id=account.id,
        vendor_id=account.vendor_id,
        credential_type="cursor_api_key",
        encrypted_value="enc",
        key_hint="hint",
        key_role="loan",
        bound_by_member_id=env["member"].id,
        assignee_member_id=borrower.id,
    )
    session.add(cred)
    session.flush()
    loan = KeyLoan(
        source_account_id=account.id,
        credential_id=cred.id,
        borrower_member_id=borrower.id,
        baseline_used_cents=0,
        status="active",
    )
    session.add(loan)
    session.flush()
    return loan


def test_build_candidates_counts_active_loans(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    b1 = env["repo"].add_member("cnt-b1", "CntB1")
    session.flush()
    _make_loan(session, env, account, b1)

    candidates = build_lender_candidates(session, env["repo"].team_id)
    by_id = {c.account_id: c for c in candidates}
    assert by_id[account.id].active_loans == 1
    assert all(
        c.active_loans == 0 for acc_id, c in by_id.items() if acc_id != account.id
    )


def test_recommend_excludes_account_at_cap(lender_env):
    env = lender_env
    session = env["session"]
    a = env["accounts"][0]
    b1 = env["repo"].add_member("rec-b1", "RecB1")
    b2 = env["repo"].add_member("rec-b2", "RecB2")
    session.flush()
    _make_loan(session, env, a, b1)
    _make_loan(session, env, a, b2)

    result = recommend_lender_for_borrower(session, env["repo"].team_id)
    assert result is not None
    assert result["account_id"] != a.id
    assert result["active_loans"] == 0


def test_recommend_respects_excluded_account_ids(lender_env):
    env = lender_env
    a = env["accounts"][0]
    result = recommend_lender_for_borrower(
        env["session"],
        env["repo"].team_id,
        exclude_account_ids={acc.id for acc in env["accounts"] if acc.id != a.id},
    )
    assert result is not None
    assert result["account_id"] == a.id


def test_recommend_returns_none_when_all_accounts_full(lender_env):
    env = lender_env
    session = env["session"]
    borrowers = [
        env["repo"].add_member(f"full-b{i}", f"FullB{i}")
        for i in range(len(env["accounts"]) * 2)
    ]
    session.flush()
    for idx, acc in enumerate(env["accounts"]):
        _make_loan(session, env, acc, borrowers[idx * 2])
        _make_loan(session, env, acc, borrowers[idx * 2 + 1])

    assert recommend_lender_for_borrower(session, env["repo"].team_id) is None


def test_expire_loans_when_renews_on_passed(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    loan = _make_loan(session, env, account, env["member"])
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date.today() + timedelta(days=20),
        renews_on=date.today() - timedelta(days=1),
    )

    svc = KeyLoanService(session, TEST_KEY)
    assert svc.expire_loans_on_reset() == 1
    assert loan.status == "expired"


def test_expire_skips_loans_before_deadline(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    loan = _make_loan(session, env, account, env["member"])
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date.today() + timedelta(days=20),
        renews_on=date.today() + timedelta(days=5),
    )

    svc = KeyLoanService(session, TEST_KEY)
    assert svc.expire_loans_on_reset() == 0
    assert loan.status == "active"


def test_loan_payload_exposes_loan_expires_on(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    loan = _make_loan(session, env, account, env["member"])
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date(2026, 8, 1),
        renews_on=date(2026, 7, 25),
    )

    payload = loan_payload(loan, session)
    assert payload["loan_expires_on"] == "2026-07-25"


def test_read_self_loan_shows_expire_date(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    borrower = env["member"]
    _make_loan(session, env, account, borrower)
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date(2026, 8, 1),
        renews_on=date(2026, 7, 25),
    )
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )

    text = read_self_loan(env["repo"], config, borrower)
    assert "自动回收日：2026-07-25" in text


def test_account_loan_deadline_picks_earliest(lender_env):
    env = lender_env
    account = env["accounts"][0]
    tool_repo = env["tool_repo"]
    tool_repo.update_account(
        account.id, usage_resets_on=date(2026, 8, 1), renews_on=None
    )
    assert account_loan_deadline(account) == date(2026, 8, 1)
    tool_repo.update_account(account.id, renews_on=date(2026, 7, 25))
    assert account_loan_deadline(account) == date(2026, 7, 25)
    tool_repo.update_account(account.id, renews_on=date(2026, 9, 1))
    assert account_loan_deadline(account) == date(2026, 8, 1)
    tool_repo.update_account(account.id, usage_resets_on=None, renews_on=date(2026, 9, 1))
    assert account_loan_deadline(account) == date(2026, 9, 1)


def test_expire_marks_expired_when_remote_revoke_fails(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    mock_client = MagicMock()
    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=account.id,
        api_key="crsr_primary_key_for_expire_test_abcdefghij",
        member_id=env["member"].id,
    )
    loan = _make_loan(session, env, account, env["member"])
    cred = session.get(AiAccountCredential, loan.credential_id)
    cred.remote_key_id = 4242
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date.today() + timedelta(days=20),
        renews_on=date.today() - timedelta(days=1),
    )
    session.flush()

    mock_client.get_access_token.side_effect = Exception("account dead")
    svc = KeyLoanService(session, TEST_KEY, cursor_client=mock_client)
    assert svc.expire_loans_on_reset() == 1
    assert loan.status == "expired"
