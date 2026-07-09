from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sqlalchemy import select

from pulse.bot.reminders.scheduler import ReminderService, build_scheduler
from pulse.config import AppConfig, CollectionConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.ingestion.credentials import CredentialService
from pulse.storage.models import AiAccountCredential, Base, ReminderLog
from pulse.tool_center.reminders import build_daily_nudge_targets
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def reminder_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        collection=CollectionConfig(
            reminders_enabled=True,
            start_day=1,
            deadline_day=28,
            timezone="Asia/Shanghai",
        ),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = sf()
    team, repo = make_team_repo(session)
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")
    member = repo.add_member("u1", "Alice")
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_accounts = [a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor"]
    for account in cursor_accounts:
        tool_repo.update_account(account.id, primary_member_id=member.id, status="trial")
    repo.commit()
    session.close()

    return {
        "config": config,
        "sf": sf,
        "team_id": team.id,
        "member": member,
        "owner": owner,
        "cursor_account": cursor_accounts[0],
    }


def test_build_daily_nudge_targets_cursor_no_credential(reminder_env):
    session = reminder_env["sf"]()
    tool_repo = ToolCenterRepository(session, reminder_env["team_id"])
    targets = build_daily_nudge_targets(tool_repo, "2026-06")
    session.close()

    kinds = {t.kind for t in targets}
    assert "no_credential" in kinds
    assert "primary_member" not in kinds


def _bind_all_cursor_credentials(session, reminder_env):
    member = reminder_env["member"]
    tool_repo = ToolCenterRepository(session, reminder_env["team_id"])
    cred_service = CredentialService(session, TEST_KEY, cursor_client=MagicMock())
    for account in tool_repo.list_active_accounts():
        if account.vendor.slug != "cursor":
            continue
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_test_api_key_abcdefghijklmnop",
            member_id=member.id,
        )


def test_build_daily_nudge_targets_cursor_sync_failed(reminder_env):
    session = reminder_env["sf"]()
    _bind_all_cursor_credentials(session, reminder_env)
    account = reminder_env["cursor_account"]
    cred = session.scalar(
        select(AiAccountCredential).where(
            AiAccountCredential.account_id == account.id
        )
    )
    cred.last_sync_status = "failed"
    session.commit()

    tool_repo = ToolCenterRepository(session, reminder_env["team_id"])
    targets = build_daily_nudge_targets(tool_repo, "2026-06")
    session.close()

    assert any(t.kind == "sync_failed" for t in targets)
    assert not any(t.kind == "no_credential" for t in targets)


def test_build_daily_nudge_targets_skips_synced_cursor(reminder_env):
    session = reminder_env["sf"]()
    _bind_all_cursor_credentials(session, reminder_env)
    creds = session.scalars(select(AiAccountCredential)).all()
    now = datetime.now(timezone.utc)
    for cred in creds:
        cred.last_sync_status = "success"
        cred.last_sync_at = now
    session.commit()

    tool_repo = ToolCenterRepository(session, reminder_env["team_id"])
    targets = build_daily_nudge_targets(tool_repo, "2026-06")
    session.close()

    cursor_targets = [
        t for t in targets if t.account.vendor and t.account.vendor.slug == "cursor"
    ]
    assert cursor_targets == []


def test_build_daily_nudge_targets_non_cursor_unsubmitted(reminder_env):
    session = reminder_env["sf"]()
    tool_repo = ToolCenterRepository(session, reminder_env["team_id"])
    member = reminder_env["member"]
    vendor = tool_repo.get_vendor_by_slug("zhipu")
    plan = next(p for p in tool_repo.list_plans(vendor.id) if p.slug == "glm_coding_lite")
    tool_repo.create_account(
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="zhipu-reminder@company.com",
        status="trial",
        primary_member_id=member.id,
    )
    session.commit()

    targets = build_daily_nudge_targets(tool_repo, "2026-06")
    session.close()

    assert any(t.kind == "primary_member" for t in targets)


def test_send_collection_start_mentions_api_key(reminder_env):
    send_group = MagicMock()
    service = ReminderService(reminder_env["config"], MagicMock(), send_group, MagicMock())
    service.send_collection_start("2026-06")
    text = send_group.call_args[0][0]
    assert "API Key" in text
    assert "CSV" not in text or "无需再上传 CSV" in text


def test_build_scheduler_includes_daily_cursor_sync(reminder_env):
    scheduler = build_scheduler(reminder_env["config"], MagicMock(), MagicMock(), MagicMock())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "daily_cursor_sync" in job_ids


def test_run_daily_cursor_sync_calls_service(reminder_env):
    session = reminder_env["sf"]()
    _bind_all_cursor_credentials(session, reminder_env)
    session.commit()
    session.close()

    service = ReminderService(reminder_env["config"], reminder_env["sf"], MagicMock(), MagicMock())
    with patch("pulse.bot.reminders.scheduler.CursorSyncService") as mock_sync_cls:
        mock_sync_cls.return_value.sync_account.return_value = MagicMock()
        synced = service.run_daily_cursor_sync()
    assert synced == 3
    assert mock_sync_cls.return_value.sync_account.call_count == 3
