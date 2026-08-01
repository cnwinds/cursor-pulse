"""Tests for loan source reassignment (keep pka_)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from pulse.ingestion.credentials import CredentialService
from pulse.proxy.keys import hash_proxy_key
from pulse.storage.models import AccountQuotaSnapshot, AiAccountCredential, KeyLoan
from pulse.tool_center.key_loans import (
    DELIVERY_PROXY_ALIAS,
    KeyLoanError,
    finalize_reassign_old_remote_revoke,
    issue_loan_key,
    reassign_loan_source,
    reveal_loan_user_key,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.auth_tokens import create_access_token
from tests.conftest import mock_cursor_key_exchange
from tests.test_quota_api import TEST_KEY, _headers

pytest_plugins = ["tests.test_quota_api"]


def _add_snapshot(session, account_id: str, *, used_cents: int = 1000) -> None:
    session.add(
        AccountQuotaSnapshot(
            account_id=account_id,
            captured_at=datetime.now(timezone.utc),
            cycle_start=date(2026, 7, 1),
            cycle_end=date(2026, 8, 1),
            limit_cents=7000,
            used_cents=used_cents,
            remaining_cents=7000 - used_cents,
            total_pct=round(used_cents / 70.0, 1),
        )
    )


def _prepare_two_lenders(session, env, mock_client):
    owner = env["owner"]
    tool_repo = ToolCenterRepository(session, owner.team_id)
    accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    source_a = env["cursor_account"]
    source_b = next(a for a in accounts if a.id != source_a.id)
    _add_snapshot(session, source_b.id, used_cents=500)

    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.side_effect = [
        {"apiKey": "crsr_loan_key_on_account_a_xxxxx"},
        {"apiKey": "crsr_loan_key_on_account_b_yyyyy"},
    ]
    mock_client.list_user_api_keys.side_effect = [
        [{"id": 11, "name": "pulse-loan-Borrower"}],
        [{"id": 22, "name": "pulse-loan-Borrower"}],
    ]

    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    mock_cursor_key_exchange(mock_client, email=source_a.account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=source_a.id,
        api_key="crsr_primary_key_for_a_abcdefghijklmnop",
        member_id=owner.id,
    )
    mock_cursor_key_exchange(mock_client, email=source_b.account_identifier.lower())
    cred_service.bind_cursor_api_key(
        account_id=source_b.id,
        api_key="crsr_primary_key_for_b_abcdefghijklmnop",
        member_id=owner.id,
    )
    session.flush()
    return source_a, source_b


@patch("pulse.tool_center.key_loan_store.CursorApiClient")
def test_reassign_keeps_pka_and_switches_source(mock_client_cls, quota_env):
    env = quota_env
    session = env["session_factory"]()
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    source_a, source_b = _prepare_two_lenders(session, env, mock_client)

    issued = issue_loan_key(
        session,
        TEST_KEY,
        team_id=env["owner"].team_id,
        source_account_id=source_a.id,
        borrower_member_id=env["borrower"].id,
        bound_by_member_id=env["owner"].id,
        cursor_client=mock_client,
    )
    assert issued["api_key"].startswith("pka_")
    pka = issued["api_key"]
    loan_id = issued["loan_id"]
    loan = session.get(KeyLoan, loan_id)
    old_cred_id = loan.credential_id
    old_hash = loan.alias_key_hash

    result = reassign_loan_source(
        session,
        TEST_KEY,
        team_id=env["owner"].team_id,
        loan_id=loan_id,
        new_source_account_id=source_b.id,
        bound_by_member_id=env["owner"].id,
        cursor_client=mock_client,
    )
    session.flush()
    # Remote revoke is deferred until after DB commit.
    mock_client.revoke_user_api_key.assert_not_called()
    finalize_reassign_old_remote_revoke(
        session, TEST_KEY, result, cursor_client=mock_client
    )

    loan = session.get(KeyLoan, loan_id)
    assert loan.source_account_id == source_b.id
    assert loan.credential_id != old_cred_id
    assert loan.alias_key_hash == old_hash
    assert reveal_loan_user_key(loan, TEST_KEY, session) == pka
    assert result["old_source_account_id"] == source_a.id
    assert result["source_account_id"] == source_b.id
    assert result["old_remote_revoked"] is True
    assert "_pending_old_remote_revoke" not in result
    assert loan.baseline_used_cents == 500

    old_cred = session.get(AiAccountCredential, old_cred_id)
    assert old_cred is not None
    assert old_cred.status == "revoked"
    mock_client.revoke_user_api_key.assert_called()
    session.close()


@patch("pulse.tool_center.key_loan_store.CursorApiClient")
def test_reassign_rejects_same_account(mock_client_cls, quota_env):
    env = quota_env
    session = env["session_factory"]()
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    source_a, _source_b = _prepare_two_lenders(session, env, mock_client)

    issued = issue_loan_key(
        session,
        TEST_KEY,
        team_id=env["owner"].team_id,
        source_account_id=source_a.id,
        borrower_member_id=env["borrower"].id,
        bound_by_member_id=env["owner"].id,
        cursor_client=mock_client,
    )
    with pytest.raises(KeyLoanError, match="相同"):
        reassign_loan_source(
            session,
            TEST_KEY,
            team_id=env["owner"].team_id,
            loan_id=issued["loan_id"],
            new_source_account_id=source_a.id,
            bound_by_member_id=env["owner"].id,
            cursor_client=mock_client,
        )
    session.close()


@patch("pulse.tool_center.key_loan_store.CursorApiClient")
def test_reassign_source_api(mock_client_cls, quota_env):
    env = quota_env
    client = env["client"]
    config = env["config"]
    owner = env["owner"]
    borrower = env["borrower"]
    token = create_access_token(config, owner)

    session = env["session_factory"]()
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    source_a, source_b = _prepare_two_lenders(session, env, mock_client)
    session.commit()

    with patch("pulse.tool_center.key_loan_store.CursorApiClient", return_value=mock_client):
        issue = client.post(
            f"/api/v2/accounts/{source_a.id}/loan-key",
            headers=_headers(token),
            json={"borrower_member_id": borrower.id, "auto_revoke_on_reset": True},
        )
        assert issue.status_code == 200, issue.text
        loan_id = issue.json()["loan_id"]
        pka = issue.json()["api_key"]

        reassigned = client.post(
            f"/api/v2/loans/{loan_id}/reassign-source",
            headers=_headers(token),
            json={"source_account_id": source_b.id},
        )
        assert reassigned.status_code == 200, reassigned.text
        body = reassigned.json()
        assert body["source_account_id"] == source_b.id
        assert body["old_source_account_id"] == source_a.id
        assert body["delivery_mode"] == DELIVERY_PROXY_ALIAS

        s2 = env["session_factory"]()
        loan = s2.get(KeyLoan, loan_id)
        assert reveal_loan_user_key(loan, TEST_KEY, s2) == pka
        assert hash_proxy_key(pka) == loan.alias_key_hash
        s2.close()
    session.close()
