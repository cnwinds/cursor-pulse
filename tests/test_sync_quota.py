from __future__ import annotations

import base64
import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync import CursorSyncService, _apply_period_usage
from pulse.integrations.cursor_api import map_usage_event
from pulse.storage.db import init_db
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_apply_period_usage_writes_snapshot_and_resets_on(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    period_usage = json.loads((FIXTURES / "cursor_period_usage.json").read_text())

    snap = _apply_period_usage(session, account, period_usage)
    session.commit()

    assert snap is not None
    assert account.usage_resets_on is not None
    assert account.resets_on_source == "api"

    stored = session.scalar(
        select(AccountQuotaSnapshot).where(AccountQuotaSnapshot.account_id == account.id)
    )
    assert stored is not None
    assert stored.limit_cents == 7000
    assert stored.used_cents == 3500


def test_apply_period_usage_respects_manual_locked(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    manual_date = date(2026, 12, 25)
    account.usage_resets_on = manual_date
    account.resets_on_source = "manual-locked"
    session.commit()

    period_usage = json.loads((FIXTURES / "cursor_period_usage.json").read_text())
    _apply_period_usage(session, account, period_usage)
    session.commit()

    assert account.usage_resets_on == manual_date
    assert account.resets_on_source == "manual-locked"


def test_sync_writes_quota_snapshot(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(
        team_id=team.id,
        display_name="Sync Tester",
        dingtalk_user_id="dt-sync-quota",
        status="active",
    )
    session.add(member)
    session.flush()
    cursor_account.primary_member_id = member.id
    session.commit()

    mock_client = MagicMock()
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=cursor_account.account_identifier.lower())
    mock_client.exchange_api_key.return_value = "session-token"
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    dto = map_usage_event(raw_event)
    mock_client.iter_filtered_usage_events.return_value = iter([dto])

    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=cursor_account.id,
        api_key="crsr_test_api_key_abcdefghijklmnop",
        member_id=member.id,
    )

    sync_service = CursorSyncService(session, TEST_KEY, cursor_client=mock_client)
    sync_service.sync_account(cursor_account.id, channel="scheduler")

    snap = session.scalar(
        select(AccountQuotaSnapshot).where(AccountQuotaSnapshot.account_id == cursor_account.id)
    )
    assert snap is not None
    account = session.get(AiAccount, cursor_account.id)
    assert account.usage_resets_on is not None


def test_sync_backfills_empty_account_identifier_via_get_me(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    cursor_account.account_identifier = ""
    member = Member(
        team_id=team.id,
        display_name="Backfill Tester",
        dingtalk_user_id="dt-sync-backfill",
        status="active",
    )
    session.add(member)
    session.flush()
    cursor_account.primary_member_id = member.id
    session.commit()

    mock_client = MagicMock()
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=None)
    mock_client.get_me.return_value = {"email": "backfill@example.com"}
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    mock_client.iter_filtered_usage_events.return_value = iter([map_usage_event(raw_event)])

    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=cursor_account.id,
        api_key="crsr_test_api_key_abcdefghijklmnop",
        member_id=member.id,
    )

    account = session.get(AiAccount, cursor_account.id)
    assert account.account_identifier == "backfill@example.com"

    account.account_identifier = ""
    session.commit()

    sync_service = CursorSyncService(session, TEST_KEY, cursor_client=mock_client)
    sync_service.sync_account(cursor_account.id, channel="scheduler")

    account = session.get(AiAccount, cursor_account.id)
    assert account.account_identifier == "backfill@example.com"
