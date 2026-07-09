from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from pulse.domain import CostRaw, ParseSummary, ParsedCsv, UsageEventRecord
from pulse.storage.db import init_db
from pulse.storage.models import Member, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def _record(event_date: date, cost: float, *, model: str = "premium") -> UsageEventRecord:
    return UsageEventRecord(
        event_at=datetime(event_date.year, event_date.month, event_date.day, tzinfo=timezone.utc),
        event_date=event_date,
        kind="Included",
        model=model,
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=0,
        tokens_cache_read=0,
        tokens_output=0,
        tokens_total=0,
        cost_raw=CostRaw.USAGE_BASED,
        cost_usd=Decimal(str(cost)),
        cloud_agent_id=None,
        automation_id=None,
        source_row_hash=f"h-{event_date.isoformat()}",
    )


def _parsed(records: list[UsageEventRecord]) -> ParsedCsv:
    total = sum(float(r.cost_usd) for r in records)
    return ParsedCsv(
        records=records,
        summary=ParseSummary(
            period_hint="2026-06",
            date_min=records[0].event_date,
            date_max=records[-1].event_date,
            event_count=len(records),
            total_tokens=0,
            total_cost_usd=Decimal(str(total)),
            top_models=[],
            all_included_or_free=False,
        ),
    )


def test_backfill_plan_upgrade_and_cycle_quota(session):
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    pro = next(p for p in tool_repo.list_plans() if p.slug == "pro")
    pro_plus = next(p for p in tool_repo.list_plans() if p.slug == "pro_plus")
    account = tool_repo.list_accounts()[0]
    tool_repo.update_account(
        account.id,
        plan_id=pro_plus.id,
        usage_resets_on=date(2026, 7, 24),
    )

    member = Member(
        team_id=team.id,
        dingtalk_user_id="u1",
        display_name="Alice",
        status="active",
    )
    session.add(member)
    session.flush()
    tool_repo.update_account(account.id, primary_member_id=member.id, status="dedicated")

    tool_repo.backfill_plan_upgrade(
        account.id,
        previous_plan_id=pro.id,
        effective_from=date(2026, 6, 24),
        note="Pro→Pro+ 续费升级",
    )
    session.flush()

    parsed = _parsed(
        [
            _record(date(2026, 6, 20), 15.0),
            _record(date(2026, 6, 28), 50.0),
            _record(date(2026, 7, 2), 19.0),
        ]
    )
    repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
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
    assert float(summary.primary_metric_value) == pytest.approx(84.0)
    assert float(summary.cycle_metric_value) == pytest.approx(69.0)
    assert summary.cycle_quota_usage_ratio == pytest.approx(69.0 / 70 * 100, rel=0.01)
    assert summary.quota_usage_ratio == summary.cycle_quota_usage_ratio
    assert summary.billing_cycle_start == date(2026, 6, 24)
    assert summary.billing_cycle_end == date(2026, 7, 24)
    assert float(summary.quota_denominator_snapshot) == 70.0
