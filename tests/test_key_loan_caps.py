from __future__ import annotations

import base64
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from sqlalchemy.exc import OperationalError

from pulse.config import LoanSelectionConfig
from pulse.ingestion.credentials import CredentialService
from pulse.storage.db import init_db
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.key_loans import (
    KeyLoanError,
    KeyLoanService,
    _lock_account_for_loan_issue,
    issue_loan_key,
    recommend_lender_for_borrower,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def cap_env():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    admin = repo.add_member("cap-admin", "CapAdmin")
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    lender = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    session.add(
        AccountQuotaSnapshot(
            account_id=lender.id,
            captured_at=datetime.now(timezone.utc),
            cycle_start=date(2026, 7, 1),
            cycle_end=date(2026, 8, 1),
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
        "admin": admin,
        "lender": lender,
    }
    session.close()


def _mock_client(env) -> MagicMock:
    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_cap_test_loan_key"}
    mock_client.list_user_api_keys.return_value = [{"id": 42, "name": "pulse-loan"}]
    mock_cursor_key_exchange(
        mock_client, email=env["lender"].account_identifier.lower()
    )
    return mock_client


def _bind_lender_primary(env, mock_client) -> None:
    cred_service = CredentialService(env["session"], TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=env["lender"].id,
        api_key="crsr_primary_key_for_cap_test_abcdefghij",
        member_id=env["admin"].id,
    )
    env["session"].flush()


def _add_borrower(env, suffix: str):
    member = env["repo"].add_member(f"cap-b{suffix}", f"CapB{suffix}")
    env["session"].flush()
    return member


def _issue(env, mock_client, borrower, loan_selection=None):
    return issue_loan_key(
        env["session"],
        TEST_KEY,
        team_id=env["repo"].team_id,
        source_account_id=env["lender"].id,
        borrower_member_id=borrower.id,
        bound_by_member_id=env["admin"].id,
        cursor_client=mock_client,
        loan_selection=loan_selection,
    )


def test_two_loans_allowed_then_third_rejected(cap_env):
    env = cap_env
    mock_client = _mock_client(env)
    _bind_lender_primary(env, mock_client)
    b1, b2, b3 = (_add_borrower(env, str(i)) for i in (1, 2, 3))

    first = _issue(env, mock_client, b1)
    second = _issue(env, mock_client, b2)
    assert first["loan_id"] != second["loan_id"]

    with pytest.raises(KeyLoanError, match="名额已满"):
        _issue(env, mock_client, b3)

    loan_svc = KeyLoanService(env["session"], TEST_KEY, cursor_client=mock_client)
    assert len(loan_svc.list_active_loans()) == 2


def test_cap_is_configurable(cap_env):
    env = cap_env
    mock_client = _mock_client(env)
    _bind_lender_primary(env, mock_client)
    b1 = _add_borrower(env, "1")
    b2 = _add_borrower(env, "2")
    selection = LoanSelectionConfig(max_active_loans_per_account=1)

    _issue(env, mock_client, b1, loan_selection=selection)
    with pytest.raises(KeyLoanError, match="名额已满"):
        _issue(env, mock_client, b2, loan_selection=selection)


def test_recommend_then_full_account_is_rejected_at_issue(cap_env):
    env = cap_env
    mock_client = _mock_client(env)
    _bind_lender_primary(env, mock_client)
    b1, b2, b3 = (_add_borrower(env, str(i)) for i in (1, 2, 3))

    lender = recommend_lender_for_borrower(env["session"], env["repo"].team_id)
    assert lender is not None
    assert lender["account_id"] == env["lender"].id

    _issue(env, mock_client, b1)
    _issue(env, mock_client, b2)
    # 推荐发生在名额占满之前：发放必须在事务内复查，而不是信任推荐结果
    with pytest.raises(KeyLoanError, match="名额已满"):
        _issue(env, mock_client, b3)


def test_lock_timeout_translated_to_business_error():
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "sqlite"
    session.execute.side_effect = OperationalError(
        "UPDATE ai_accounts", {}, Exception("database is locked")
    )
    with pytest.raises(KeyLoanError, match="系统繁忙"):
        _lock_account_for_loan_issue(session, "acc-1")
