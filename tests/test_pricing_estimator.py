from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pulse.domain import CostRaw, UsageEventRecord
from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.pricing.estimator import aggregate_cursor_billing, aggregate_pool_costs, estimate_event_record, resolve_cost_fields
from pulse.pricing.types import TokenRates, estimate_token_cost
from pulse.pricing.cursor_tables import CURSOR_PRICING_V2026_06
from pulse.storage.db import init_db
from pulse.storage.models import UsageRecord
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage import build_usage_summary
from tests.conftest import make_team_repo

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"


def _included_record(**kwargs) -> UsageEventRecord:
    defaults = dict(
        event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        event_date=datetime(2026, 6, 1).date(),
        kind="Included",
        model="auto",
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=1_000_000,
        tokens_cache_read=2_000_000,
        tokens_output=100_000,
        tokens_total=3_100_000,
        cost_raw=CostRaw.INCLUDED,
        cost_usd=Decimal("0"),
        cloud_agent_id=None,
        automation_id=None,
        source_row_hash="hash1",
    )
    defaults.update(kwargs)
    return UsageEventRecord(**defaults)


def test_included_premium_uses_api_pool_rates():
    base_tokens = dict(
        tokens_input_cache_write=0,
        tokens_input_no_cache=1_000_000,
        tokens_cache_read=2_000_000,
        tokens_output=100_000,
        tokens_total=3_100_000,
    )
    auto_est = estimate_event_record(_included_record(model="auto", **base_tokens))
    premium_est = estimate_event_record(_included_record(model="premium", **base_tokens))
    assert auto_est is not None and premium_est is not None
    assert premium_est.cost_usd != auto_est.cost_usd
    assert premium_est.pricing_rule == "included:api"
    assert auto_est.pricing_rule == "included:auto_composer"


def test_estimate_auto_included_row_uses_token_rates():
    rec = _included_record()
    estimate = estimate_event_record(rec)
    assert estimate is not None
    assert estimate.cost_usd > 0
    assert estimate.pricing_rule == "included:auto_composer"


def test_auto_million_tokens_matches_cursor_flat_rates():
    estimate = estimate_token_cost(
        model="auto",
        max_mode=False,
        tokens_input_no_cache=1_000_000,
        tokens_input_cache_write=0,
        tokens_cache_read=0,
        tokens_output=0,
        table=CURSOR_PRICING_V2026_06,
    )
    assert estimate is not None
    assert estimate.cost_usd == pytest.approx(1.25, rel=1e-6)


def test_resolve_cost_fields_for_included():
    fields = resolve_cost_fields(_included_record())
    assert fields["cost_basis"] == "estimated"
    assert fields["cost_usd"] == 0.0
    assert fields["cost_estimated_usd"] > 0


def test_sample_csv_pool_spend_exceeds_reported_only():
    parsed = parse_usage_events_csv(SAMPLE)
    records = []
    for rec in parsed.records:
        fields = resolve_cost_fields(rec)
        records.append(
            UsageRecord(
                ingestion_id="s1",
                member_id="m1",
                event_at=rec.event_at,
                event_date=rec.event_date,
                kind=rec.kind,
                model=rec.model,
                max_mode=rec.max_mode,
                tokens_input_cache_write=rec.tokens_input_cache_write,
                tokens_input_no_cache=rec.tokens_input_no_cache,
                tokens_cache_read=rec.tokens_cache_read,
                tokens_output=rec.tokens_output,
                tokens_total=rec.tokens_total,
                cost_raw=rec.cost_raw.value,
                cost_usd=fields["cost_usd"],
                cost_estimated_usd=fields["cost_estimated_usd"],
                cost_basis=fields["cost_basis"],
            )
        )

    pools = aggregate_cursor_billing(records)
    assert pools["reported_spend_usd"] == 0.0
    assert pools["estimated_included_spend_usd"] > 0
    assert pools["pool_spend_usd"] == pools["estimated_included_spend_usd"]
    assert pools["cursor_pools"]["auto_composer"]["spend_usd"] > 0
    assert pools["external_models"]


def test_build_usage_summary_uses_pool_spend_for_pro_plus(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    from pulse.tool_center.repository import ToolCenterRepository

    tool_repo = ToolCenterRepository(session, team.id)
    plan = next(p for p in tool_repo.list_plans() if p.slug == "pro_plus")

    parsed = parse_usage_events_csv(SAMPLE)
    records = []
    for rec in parsed.records:
        fields = resolve_cost_fields(rec)
        records.append(
            UsageRecord(
                ingestion_id="s1",
                member_id="m1",
                event_at=rec.event_at,
                event_date=rec.event_date,
                kind=rec.kind,
                model=rec.model,
                max_mode=rec.max_mode,
                tokens_input_cache_write=rec.tokens_input_cache_write,
                tokens_input_no_cache=rec.tokens_input_no_cache,
                tokens_cache_read=rec.tokens_cache_read,
                tokens_output=rec.tokens_output,
                tokens_total=rec.tokens_total,
                cost_raw=rec.cost_raw.value,
                cost_usd=fields["cost_usd"],
                cost_estimated_usd=fields["cost_estimated_usd"],
                cost_basis=fields["cost_basis"],
            )
        )

    summary = build_usage_summary(plan=plan, records=records)
    assert summary["primary_metric_value"] > 0
    assert summary["estimated_included_spend_usd"] > 0
    assert summary["cursor_pools"] is not None
    assert summary["cursor_pools"]["api"]["spend_usd"] == summary["primary_metric_value"]
    assert summary["quota_usage_ratio"] is not None
    assert summary["quota_usage_ratio"] == pytest.approx(
        summary["primary_metric_value"] / 70 * 100, rel=0.01
    )
    assert summary["external_models"]
    assert "GLM-5.1" in summary["external_models"]


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()
