from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pulse.ingestion.on_demand import (
    OnDemandEnforceResult,
    enforce_on_demand_disabled,
    format_on_demand_admin_alert,
)
from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync import CursorSyncService
from pulse.integrations.cursor_api import CursorApiClient, map_usage_event
from pulse.storage.db import init_db
from pulse.storage.models import Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_get_hard_limit_posts_dashboard_method():
    client = CursorApiClient()
    client._post_dashboard = MagicMock(
        return_value={"hardLimit": 0, "noUsageBasedAllowed": True}
    )
    data = client.get_hard_limit("tok", api_key="crsr_x")
    assert data["noUsageBasedAllowed"] is True
    client._post_dashboard.assert_called_once_with(
        "tok", "GetHardLimit", {}, api_key="crsr_x"
    )


def test_set_hard_limit_posts_disable_payload():
    client = CursorApiClient()
    client._post_dashboard = MagicMock(return_value={})
    client.set_hard_limit(
        "tok",
        hard_limit=0,
        no_usage_based_allowed=True,
        api_key="crsr_x",
    )
    client._post_dashboard.assert_called_once_with(
        "tok",
        "SetHardLimit",
        {"hardLimit": 0, "noUsageBasedAllowed": True},
        api_key="crsr_x",
    )


def test_enforce_already_disabled():
    client = MagicMock()
    client.get_hard_limit.return_value = {
        "hardLimit": 0,
        "noUsageBasedAllowed": True,
    }
    result = enforce_on_demand_disabled(client, "tok", api_key="crsr_x")
    assert result.status == "already_disabled"
    client.set_hard_limit.assert_not_called()


def test_enforce_disables_when_enabled():
    client = MagicMock()
    client.get_hard_limit.return_value = {
        "hardLimit": 50,
        "noUsageBasedAllowed": False,
    }
    result = enforce_on_demand_disabled(client, "tok", api_key="crsr_x")
    assert result.status == "disabled_now"
    client.set_hard_limit.assert_called_once_with(
        "tok",
        hard_limit=0,
        no_usage_based_allowed=True,
        api_key="crsr_x",
    )


def test_enforce_check_failed_does_not_raise():
    client = MagicMock()
    client.get_hard_limit.side_effect = RuntimeError("boom")
    result = enforce_on_demand_disabled(client, "tok")
    assert result.status == "check_failed"
    assert "boom" in (result.error or "")


def test_enforce_disable_failed():
    client = MagicMock()
    client.get_hard_limit.return_value = {"noUsageBasedAllowed": False, "hardLimit": 10}
    client.set_hard_limit.side_effect = RuntimeError("set failed")
    result = enforce_on_demand_disabled(client, "tok", api_key="k")
    assert result.status == "disable_failed"
    assert "set failed" in (result.error or "")


def test_format_on_demand_admin_alert_disabled_now():
    account = MagicMock()
    account.shared_note = "共享-A"
    account.account_identifier = "a@example.com"
    account.id = "acc-1"
    text = format_on_demand_admin_alert(
        account,
        OnDemandEnforceResult(status="disabled_now", previous_hard_limit=50),
    )
    assert "On-Demand" in text
    assert "已自动关闭" in text
    assert "共享-A" in text
    assert "a@example.com" in text


def test_sync_disables_on_demand_and_notifies(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(
        team_id=team.id,
        display_name="OD Tester",
        dingtalk_user_id="dt-od",
        status="active",
    )
    session.add(member)
    session.flush()
    cursor_account.primary_member_id = member.id
    session.commit()

    mock_client = MagicMock()
    mock_cursor_key_exchange(mock_client, email=cursor_account.account_identifier.lower())
    mock_client.get_access_token.return_value = "session-token"
    mock_client.get_hard_limit.return_value = {
        "hardLimit": 100,
        "noUsageBasedAllowed": False,
    }
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    mock_client.iter_filtered_usage_events.return_value = iter([map_usage_event(raw_event)])

    notify = MagicMock()
    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=cursor_account.id,
        api_key="crsr_test_api_key_abcdefghijklmnop",
        member_id=member.id,
    )

    sync_service = CursorSyncService(
        session, TEST_KEY, cursor_client=mock_client, on_demand_notify=notify
    )
    sync_service.sync_account(cursor_account.id, channel="scheduler")

    mock_client.set_hard_limit.assert_called_once()
    notify.assert_called_once()
    account_arg, result_arg = notify.call_args[0]
    assert account_arg.id == cursor_account.id
    assert result_arg.status == "disabled_now"


def test_sync_continues_when_on_demand_check_fails(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(
        team_id=team.id,
        display_name="OD Fail",
        dingtalk_user_id="dt-od-fail",
        status="active",
    )
    session.add(member)
    session.flush()
    cursor_account.primary_member_id = member.id
    session.commit()

    mock_client = MagicMock()
    mock_cursor_key_exchange(mock_client, email=cursor_account.account_identifier.lower())
    mock_client.get_access_token.return_value = "session-token"
    mock_client.get_hard_limit.side_effect = RuntimeError("hard limit unavailable")
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

    sync_service = CursorSyncService(session, TEST_KEY, cursor_client=mock_client)
    result = sync_service.sync_account(cursor_account.id, channel="scheduler")
    assert result.status == "confirmed"
    mock_client.get_current_period_usage.assert_called()
