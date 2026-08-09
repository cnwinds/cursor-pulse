from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from pulse.tool_center.snapshot_headroom import (
    api_quota_ratio_for_period,
    live_api_pct_for_period,
    snapshot_latest_calendar_period,
    stored_quota_ratio,
)


def _snapshot(*, start: date, end: date, api_pct: float | None) -> SimpleNamespace:
    return SimpleNamespace(cycle_start=start, cycle_end=end, api_pct=api_pct)


def test_snapshot_latest_calendar_period_uses_inclusive_last_day():
    snap = _snapshot(start=date(2026, 7, 9), end=date(2026, 8, 9), api_pct=90.0)
    assert snapshot_latest_calendar_period(snap) == "2026-08"
    june_cycle = _snapshot(start=date(2026, 6, 1), end=date(2026, 7, 1), api_pct=90.0)
    assert snapshot_latest_calendar_period(june_cycle) == "2026-06"


def test_live_api_pct_only_latest_month_of_cycle():
    snap = _snapshot(start=date(2026, 7, 9), end=date(2026, 8, 9), api_pct=90.0)
    assert live_api_pct_for_period(snap, "2026-08") == 90.0
    assert live_api_pct_for_period(snap, "2026-07") is None
    assert live_api_pct_for_period(snap, "2026-06") is None
    assert live_api_pct_for_period(None, "2026-08") is None
    assert live_api_pct_for_period(snap, "") is None
    missing = _snapshot(start=date(2026, 7, 9), end=date(2026, 8, 9), api_pct=None)
    assert live_api_pct_for_period(missing, "2026-08") is None


def test_api_quota_ratio_uses_cycle_ratio_when_not_live_month():
    summary = SimpleNamespace(cycle_quota_usage_ratio=40.0, quota_usage_ratio=10.0)
    snap = _snapshot(start=date(2026, 7, 9), end=date(2026, 8, 9), api_pct=90.0)
    assert stored_quota_ratio(summary) == 40.0
    assert api_quota_ratio_for_period(summary, snap, "2026-07") == 40.0
    assert api_quota_ratio_for_period(summary, snap, "2026-08") == 90.0
    fallback = SimpleNamespace(cycle_quota_usage_ratio=None, quota_usage_ratio=12.5)
    assert api_quota_ratio_for_period(fallback, snap, "2026-07") == 12.5
