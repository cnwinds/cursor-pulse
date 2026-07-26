from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from pulse.channels.reminders.scheduler import SyncSchedulerService, build_scheduler
from pulse.config import AppConfig, CollectionConfig, CredentialConfig, CursorSyncConfig
from pulse.storage.db import init_db
from pulse.storage.models import Member, UsageSummary
from pulse.tool_center.reminders import build_daily_nudge_targets, format_deadline_group_message
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage import compute_quota_ratio, model_family
from tests.conftest import ingest_cursor_fixture, make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_model_family_mapping():
    assert model_family("claude-3.5-sonnet") == "Claude"
    assert model_family("gpt-4o") == "GPT"
    assert model_family("glm-4") == "GLM"


def test_pro_plus_quota_ratio_uses_70_denominator():
    from pulse.storage.models import AiPlan

    plan = AiPlan(
        vendor_id="v1",
        plan_name="Pro+",
        slug="pro_plus",
        billing_type="fixed_monthly_pool",
        price_amount=60,
        price_currency="USD",
        quota_ratio_enabled=True,
        quota_denominator=70,
    )
    assert compute_quota_ratio(plan, 66.5) == 95.0


def test_seed_v2_catalog_idempotent(session):
    team, _ = make_team_repo(session)
    first = seed_v2_catalog(session, team)
    session.flush()
    second = seed_v2_catalog(session, team)
    assert first["vendors"] == 1
    assert first["plans"] == 3
    assert first["accounts"] == 3
    assert second == {"vendors": 0, "plans": 0, "accounts": 0}


def test_account_submission_creates_usage_summary(session):
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    account = tool_repo.list_accounts()[0]
    member = Member(
        team_id=team.id,
        channel_user_id="u-primary",
        display_name="Primary",
        status="active",
    )
    session.add(member)
    session.flush()
    tool_repo.update_account(account.id, primary_member_id=member.id, status="trial")

    ingest_cursor_fixture(
        session,
        team_id=team.id,
        account_id=account.id,
        vendor_id=account.vendor_id,
        member_id=member.id,
        period="2026-06",
    )
    session.commit()

    summary = session.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == account.id,
            UsageSummary.period == "2026-06",
        )
    )
    assert summary is not None
    assert summary.sync_source == "api"


def test_daily_nudge_targets_primary_and_admin(session):
    team, _repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    accounts = tool_repo.list_accounts()
    cursor_account = next(a for a in accounts if a.vendor.slug == "cursor")
    primary = Member(
        team_id=team.id,
        channel_user_id="u1",
        display_name="Alice",
        status="active",
    )
    session.add(primary)
    session.flush()
    tool_repo.update_account(cursor_account.id, primary_member_id=primary.id, status="trial")
    tool_repo.update_account(
        next(a for a in accounts if a.vendor.slug == "cursor" and a.id != cursor_account.id).id,
        primary_member_id=None,
    )

    targets = build_daily_nudge_targets(tool_repo, "2026-06")
    kinds = {t.kind for t in targets}
    assert "admin_no_primary" in kinds
    assert "no_credential" in kinds


def test_deadline_message_is_anonymous():
    text = format_deadline_group_message(
        period="2026-06",
        total_accounts=3,
        submitted_count=1,
        missing_primary_count=1,
    )
    assert "2026-06" in text
    assert "1/3" in text
    assert "Alice" not in text


def test_evaluate_upgrade_after_two_months(session):
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    account = tool_repo.list_accounts()[0]
    plan = tool_repo.get_plan(account.plan_id)
    assert plan is not None

    for period, value in [("2026-05", 66.5), ("2026-06", 67.0)]:
        tool_repo.upsert_usage_summary(
            account_id=account.id,
            period=period,
            ingestion_id="sub-" + period,
            submitted_by_member_id="m1",
            summary={
                "primary_metric_value": value,
                "primary_metric_unit": "usd",
                "quota_usage_ratio": compute_quota_ratio(plan, value),
                "breakdown_by_model": {"Claude": value},
            },
        )
    session.flush()

    from pulse.tool_center.upgrade import evaluate_account_upgrade

    assert evaluate_account_upgrade(session, account.id, "2026-06") is True
    session.refresh(account)
    assert account.suggest_dedicated is True
    assert evaluate_account_upgrade(session, account.id, "2026-06") is False


def test_aggregate_account_metrics(session):
    team, _repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    account = tool_repo.list_accounts()[0]
    plan = tool_repo.get_plan(account.plan_id)
    tool_repo.upsert_usage_summary(
        account_id=account.id,
        period="2026-06",
        ingestion_id="sub1",
        submitted_by_member_id="m1",
        summary={
            "primary_metric_value": 30.0,
            "primary_metric_unit": "usd",
            "quota_usage_ratio": compute_quota_ratio(plan, 30.0),
            "breakdown_by_model": {"Claude": 20.0, "GPT": 10.0},
        },
    )
    session.flush()

    from pulse.tool_center.aggregate import aggregate_account_metrics

    metrics = aggregate_account_metrics(session, "2026-06", team_id=team.id)
    assert metrics["account_count_active"] == 3
    assert metrics["account_count_submitted"] == 1
    assert "Claude" in metrics["model_family_pct"]


def test_account_usage_resets_on(session):
    team, _repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    tool_repo = ToolCenterRepository(session, team.id)
    account = tool_repo.list_accounts()[0]
    tool_repo.update_account(account.id, usage_resets_on=date(2026, 7, 15))
    session.flush()
    session.refresh(account)
    assert account.usage_resets_on.isoformat() == "2026-07-15"
