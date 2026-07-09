from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from decimal import Decimal

from pulse.domain import CostRaw, ParseSummary, ParsedCsv, UsageEventRecord
from pulse.storage.db import init_db
from pulse.storage.models import AiPlan, Member, UsageSummary
from pulse.tool_center.reminders import build_daily_nudge_targets, format_deadline_group_message
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage import compute_quota_ratio, model_family
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def _parsed_with_cost(total: float) -> ParsedCsv:
    rec = UsageEventRecord(
        event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        event_date=datetime(2026, 6, 1).date(),
        kind="usage",
        model="claude-sonnet-4",
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=0,
        tokens_cache_read=0,
        tokens_output=0,
        tokens_total=0,
        cost_raw=CostRaw.USAGE_BASED,
        cost_usd=Decimal(str(total)),
        cloud_agent_id=None,
        automation_id=None,
        source_row_hash="abc",
    )
    summary = ParseSummary(
        period_hint="2026-06",
        date_min=rec.event_date,
        date_max=rec.event_date,
        event_count=1,
        total_tokens=0,
        total_cost_usd=Decimal(str(total)),
        top_models=[],
        all_included_or_free=False,
    )
    return ParsedCsv(records=[rec], summary=summary)


def test_model_family_mapping():
    assert model_family("claude-3.5-sonnet") == "Claude"
    assert model_family("gpt-4o") == "GPT"
    assert model_family("glm-4") == "GLM"


def test_pro_plus_quota_ratio_uses_70_denominator():
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
    assert first["vendors"] == 4
    assert first["plans"] == 6
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
        dingtalk_user_id="u-primary",
        display_name="Primary",
        status="active",
    )
    session.add(member)
    session.flush()
    tool_repo.update_account(account.id, primary_member_id=member.id, status="trial")

    repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=_parsed_with_cost(66.5),
        submit_channel="private",
        account_id=account.id,
    )
    repo.commit()

    summary = session.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == account.id,
            UsageSummary.period == "2026-06",
        )
    )
    assert summary is not None
    assert float(summary.primary_metric_value) == 66.5
    assert summary.quota_usage_ratio == 95.0
    assert summary.breakdown_by_model == {"claude-sonnet-4": 66.5}


def test_daily_nudge_targets_primary_and_admin(session):
    team, _repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    accounts = tool_repo.list_accounts()
    cursor_account = next(a for a in accounts if a.vendor.slug == "cursor")
    zhipu_vendor = tool_repo.get_vendor_by_slug("zhipu")
    zhipu_plan = next(p for p in tool_repo.list_plans(zhipu_vendor.id))
    zhipu_account = tool_repo.create_account(
        vendor_id=zhipu_vendor.id,
        plan_id=zhipu_plan.id,
        account_identifier="zhipu-nudge@test.com",
        status="trial",
    )
    primary = Member(
        team_id=team.id,
        dingtalk_user_id="u1",
        display_name="Alice",
        status="active",
    )
    session.add(primary)
    session.flush()
    tool_repo.update_account(zhipu_account.id, primary_member_id=primary.id, status="trial")
    tool_repo.update_account(cursor_account.id, primary_member_id=primary.id, status="trial")
    # another cursor account without primary triggers admin_no_primary
    tool_repo.update_account(
        next(a for a in accounts if a.vendor.slug == "cursor" and a.id != cursor_account.id).id,
        primary_member_id=None,
    )

    targets = build_daily_nudge_targets(tool_repo, "2026-06")
    kinds = {t.kind for t in targets}
    assert "primary_member" in kinds
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
    team, repo = make_team_repo(session)
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
