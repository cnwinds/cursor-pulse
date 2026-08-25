from __future__ import annotations

from types import SimpleNamespace

from pulse.ingestion.plan_infer import (
    cycle_end_from_period_usage,
    default_cursor_plan,
    infer_plan_from_period_usage,
)


def _plan(slug: str, spend_cap_usd: float):
    return SimpleNamespace(
        slug=slug,
        included_quota={"spend_cap_usd": spend_cap_usd},
        quota_denominator=spend_cap_usd,
    )


def test_infer_plan_matches_pro_plus_limit():
    plans = [_plan("pro", 20), _plan("pro_plus", 70), _plan("ultra", 400)]
    period = {"planUsage": {"limit": 7000}}
    assert infer_plan_from_period_usage(plans, period).slug == "pro_plus"


def test_infer_plan_matches_pro_limit():
    plans = [_plan("pro", 20), _plan("pro_plus", 70)]
    period = {"planUsage": {"limit": 2000}}
    assert infer_plan_from_period_usage(plans, period).slug == "pro"


def test_cycle_end_from_period_usage():
    # 1784958141000 ms → 2026-07-25 UTC
    period = {"billingCycleEnd": "1784958141000"}
    assert cycle_end_from_period_usage(period).isoformat() == "2026-07-25"


def test_cycle_end_at_from_period_usage_keeps_clock():
    from pulse.ingestion.plan_infer import cycle_end_at_from_period_usage

    period = {"billingCycleEnd": "1787566731000"}  # feong 实际重置
    at = cycle_end_at_from_period_usage(period)
    assert at is not None
    assert at.isoformat() == "2026-08-24T10:18:51+00:00"


def test_default_cursor_plan_prefers_pro():
    plans = [_plan("ultra", 400), _plan("pro", 20), _plan("pro_plus", 70)]
    assert default_cursor_plan(plans).slug == "pro"
