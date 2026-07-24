from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pulse.config import AdminConfig, AppConfig, CursorSyncConfig
from pulse.ingestion.on_demand import (
    OnDemandEnforceResult,
    enforce_on_demand_disabled,
    format_on_demand_admin_alert,
    resolve_admin_dingtalk_ids,
    resolve_on_demand_notify_dingtalk_ids,
)
from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync import CursorSyncService
from pulse.ingestion.sync_tick import _make_on_demand_notify
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
    account.status = "shared"
    account.shared_note = "试用池共享账号，请指定主使用人后提交用量"
    account.account_identifier = "a@example.com"
    account.id = "acc-1"
    text = format_on_demand_admin_alert(
        account,
        OnDemandEnforceResult(status="disabled_now", previous_hard_limit=50),
    )
    assert "On-Demand" in text
    assert "已自动关闭" in text
    assert "a@example.com" in text
    assert "账号：" not in text
    assert "请指定主使用人" not in text


def test_format_check_failed_mentions_api():
    account = MagicMock()
    account.status = "trial"
    account.shared_note = ""
    account.account_identifier = "b@example.com"
    text = format_on_demand_admin_alert(
        account,
        OnDemandEnforceResult(status="check_failed", error="404 not found"),
    )
    assert "GetHardLimit" in text
    assert "404 not found" in text
    assert "管理员" in text
    assert "账号：" not in text


def test_resolve_admin_dingtalk_ids():
    config = AppConfig(admin=AdminConfig(dingtalk_user_ids=[" a ", "b", "a", ""]))
    assert resolve_admin_dingtalk_ids(config) == ["a", "b"]


def test_check_failed_notifies_admins_only(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")

    primary = Member(
        team_id=team.id,
        display_name="Primary",
        dingtalk_user_id="dt-primary",
        status="active",
    )
    session.add(primary)
    session.flush()
    account.primary_member_id = primary.id
    session.commit()

    send = MagicMock()
    config = AppConfig(
        admin=AdminConfig(dingtalk_user_ids=["dt-admin"]),
        cursor_sync=CursorSyncConfig(
            enforce_on_demand_disabled=True,
            on_demand_notify_member_ids=[primary.id],
            on_demand_notify_primary=True,
            on_demand_notify_admins_on_api_failure=True,
        ),
    )
    notify = _make_on_demand_notify(session, config, send)
    assert notify is not None
    notify(
        account,
        OnDemandEnforceResult(status="check_failed", error="api changed"),
    )
    send.assert_called_once()
    assert send.call_args[0][0] == "dt-admin"
    assert "GetHardLimit" in send.call_args[0][1]


def test_check_failed_skips_admin_notify_when_disabled(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")

    send = MagicMock()
    config = AppConfig(
        admin=AdminConfig(dingtalk_user_ids=["dt-admin"]),
        cursor_sync=CursorSyncConfig(
            on_demand_notify_admins_on_api_failure=False,
        ),
    )
    notify = _make_on_demand_notify(session, config, send)
    notify(account, OnDemandEnforceResult(status="check_failed", error="x"))
    send.assert_not_called()


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


def test_resolve_notify_falls_back_to_admins(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")

    admin = Member(
        team_id=team.id,
        display_name="Admin",
        dingtalk_user_id="dt-admin",
        status="active",
    )
    primary = Member(
        team_id=team.id,
        display_name="Primary",
        dingtalk_user_id="dt-primary",
        status="active",
    )
    session.add_all([admin, primary])
    session.flush()
    account.primary_member_id = primary.id
    session.commit()

    config = AppConfig(
        admin=AdminConfig(dingtalk_user_ids=["dt-admin"]),
        cursor_sync=CursorSyncConfig(
            on_demand_notify_member_ids=None,
            on_demand_notify_primary=True,
        ),
    )
    ids = resolve_on_demand_notify_dingtalk_ids(session, config, account)
    assert set(ids) == {"dt-admin", "dt-primary"}


def test_resolve_notify_empty_list_skips_admin_fallback(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")

    admin = Member(
        team_id=team.id,
        display_name="Admin",
        dingtalk_user_id="dt-admin",
        status="active",
    )
    session.add(admin)
    session.commit()

    config = AppConfig(
        admin=AdminConfig(dingtalk_user_ids=["dt-admin"]),
        cursor_sync=CursorSyncConfig(
            on_demand_notify_member_ids=[],
            on_demand_notify_primary=False,
        ),
    )
    assert resolve_on_demand_notify_dingtalk_ids(session, config, account) == []


def test_sync_skips_enforce_when_disabled(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(
        team_id=team.id,
        display_name="Skip Enforce",
        dingtalk_user_id="dt-skip",
        status="active",
    )
    session.add(member)
    session.flush()
    cursor_account.primary_member_id = member.id
    session.commit()

    mock_client = MagicMock()
    mock_cursor_key_exchange(mock_client, email=cursor_account.account_identifier.lower())
    mock_client.get_access_token.return_value = "session-token"
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

    sync_service = CursorSyncService(
        session,
        TEST_KEY,
        cursor_client=mock_client,
        enforce_on_demand_disabled=False,
    )
    sync_service.sync_account(cursor_account.id, channel="scheduler")
    mock_client.get_hard_limit.assert_not_called()
    mock_client.set_hard_limit.assert_not_called()


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
