from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.config import AppConfig, CollectionConfig, CredentialConfig, CursorSyncConfig, TenantConfig, WebConfig
from pulse.report.readiness import check_period_readiness
from pulse.storage.models import AiAccountCredential, Base, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def readiness_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        collection=CollectionConfig(timezone="Asia/Shanghai", report_period_mode="previous"),
        credentials=CredentialConfig(encryption_key="test-key"),
        cursor_sync=CursorSyncConfig(readiness_sync_max_age_hours=6),
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
    member = repo.add_member("u1", "Alice")
    bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(account.id, primary_member_id=member.id, status="trial")
    repo.commit()
    session.close()
    return {"config": config, "sf": sf, "team_id": team.id, "account": account, "member": member}


def test_readiness_blocks_without_submission(readiness_env):
    session = readiness_env["sf"]()
    result = check_period_readiness(
        session,
        readiness_env["team_id"],
        "2026-06",
        readiness_env["config"],
        now=datetime(2026, 7, 2, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    session.close()
    assert result.ready is False
    assert result.issues


def test_readiness_passes_with_summary_and_fresh_sync(readiness_env):
    session = readiness_env["sf"]()
    tool_repo = ToolCenterRepository(session, readiness_env["team_id"])
    now = datetime(2026, 7, 2, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for account in tool_repo.list_active_accounts():
        tool_repo.update_account(account.id, primary_member_id=readiness_env["member"].id)
        session.add(
            UsageSummary(
                account_id=account.id,
                period="2026-06",
                primary_metric_value=10,
                primary_metric_unit="events",
            )
        )
        if account.vendor.slug == "cursor":
            session.add(
                AiAccountCredential(
                    account_id=account.id,
                    vendor_id=account.vendor_id,
                    credential_type="cursor_api_key",
                    encrypted_value="enc",
                    key_hint="crsr",
                    bound_by_member_id=readiness_env["member"].id,
                    last_sync_at=datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc),
                    last_sync_status="success",
                    sync_enabled=True,
                )
            )
    session.commit()

    result = check_period_readiness(
        session,
        readiness_env["team_id"],
        "2026-06",
        readiness_env["config"],
        now=now,
    )
    session.close()
    assert result.ready is True
