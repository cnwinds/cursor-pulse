from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from pulse.storage.db import init_db
from pulse.storage.models import UsageRecord
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage import (
    build_account_usage_summary,
    snapshot_cycle_for_period,
)
from tests.conftest import make_team_repo


def _record(*, event_date: date, model: str, cost: float, hash_suffix: str) -> UsageRecord:
    return UsageRecord(
        ingestion_id="ing-test",
        member_id="m1",
        event_at=datetime(event_date.year, event_date.month, event_date.day, tzinfo=timezone.utc),
        event_date=event_date,
        kind="On-Demand",
        model=model,
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=1000,
        tokens_cache_read=0,
        tokens_output=100,
        tokens_total=1100,
        cost_raw="usage_based",
        cost_usd=Decimal(str(cost)),
        cost_estimated_usd=None,
        cost_basis="reported",
        source_row_hash=f"h-{hash_suffix}",
    )


def test_account_usage_summary_pools_follow_billing_cycle():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    try:
        team, _ = make_team_repo(session)
        seed_v2_catalog(session, team)
        session.flush()
        from pulse.tool_center.repository import ToolCenterRepository

        tool_repo = ToolCenterRepository(session, team.id)
        plan = next(p for p in tool_repo.list_plans() if p.slug == "pro_plus")
        # resets day=24 → inferred cycle [2026-07-24, 2026-08-24)
        account = SimpleNamespace(usage_resets_on=date(2026, 8, 24))

        records = [
            _record(event_date=date(2026, 7, 20), model="gpt-5", cost=50.0, hash_suffix="old"),
            _record(event_date=date(2026, 7, 24), model="gpt-5", cost=9.0, hash_suffix="edge"),
            _record(event_date=date(2026, 7, 26), model="gpt-5", cost=3.0, hash_suffix="new"),
        ]
        summary = build_account_usage_summary(
            account=account,
            plan=plan,
            records=records,
            period="2026-07",
        )
        assert summary["billing_cycle_start"] == date(2026, 7, 24)
        assert summary["primary_metric_value"] == 12.0  # 9 + 3
        assert summary["cursor_pools"]["api"]["spend_usd"] == 12.0

        # Snapshot cycle starts 07-25 → drop the 07-24 edge spend (align with progress bar).
        summary_snap = build_account_usage_summary(
            account=account,
            plan=plan,
            records=records,
            period="2026-07",
            cycle_bounds=snapshot_cycle_for_period(
                cycle_start=date(2026, 7, 25),
                cycle_end=date(2026, 8, 25),
                period="2026-07",
            ),
        )
        assert summary_snap["billing_cycle_start"] == date(2026, 7, 25)
        assert summary_snap["billing_cycle_end"] == date(2026, 8, 25)
        assert summary_snap["primary_metric_value"] == 3.0
        assert summary_snap["cursor_pools"]["api"]["spend_usd"] == 3.0
    finally:
        session.close()
