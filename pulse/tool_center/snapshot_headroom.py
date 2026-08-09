"""Snapshot Headroom rules shared by Credential Pool Intake and Go proxy."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pulse.storage.models import AccountQuotaSnapshot, UsageSummary
from pulse.tool_center.billing_cycle import period_first_day, period_last_day

QuotaPoolKind = Literal["auto", "api", "unknown"]


def pct_quota_ok(pct: float | None) -> bool:
    """Snapshot headroom for one Quota Pool.

    Matches Go ``pctQuotaOK``: missing pct is unknown → treat as OK.
    """
    if pct is None:
        return True
    return pct < 100


def snapshot_has_any_pool_headroom(
    *,
    auto_pct: float | None,
    api_pct: float | None,
) -> bool:
    """Credential Pool Intake: at least one Quota Pool still has snapshot headroom.

    Intentionally OR (not AND). Request-time selection for an unknown pool
    still requires both buckets OK — see snapshot_quota_ok_for_pool.
    """
    return pct_quota_ok(auto_pct) or pct_quota_ok(api_pct)


def snapshot_quota_ok_for_pool(
    pool: QuotaPoolKind | str,
    *,
    auto_pct: float | None,
    api_pct: float | None,
) -> bool:
    """Match Go ``snapshotQuotaOK`` for a Quota Pool kind."""
    if pool == "auto":
        return pct_quota_ok(auto_pct)
    if pool == "api":
        return pct_quota_ok(api_pct)
    return pct_quota_ok(auto_pct) and pct_quota_ok(api_pct)


def snapshot_latest_calendar_period(snapshot: AccountQuotaSnapshot) -> str | None:
    """YYYY-MM of the last inclusive day in the snapshot cycle."""
    last_day = snapshot.cycle_end - timedelta(days=1)
    if last_day < snapshot.cycle_start:
        return None
    return f"{last_day.year:04d}-{last_day.month:02d}"


def live_api_pct_for_period(snapshot: AccountQuotaSnapshot | None, period: str) -> float | None:
    """Live ``api_pct`` only for the latest calendar month of the snapshot cycle.

    Earlier overlapping months keep stored summary ratios (no historical contamination).
    """
    if snapshot is None or snapshot.api_pct is None or not (period or "").strip():
        return None
    start = period_first_day(period)
    end = period_last_day(period)
    if not (snapshot.cycle_start <= end and snapshot.cycle_end > start):
        return None
    if period != snapshot_latest_calendar_period(snapshot):
        return None
    return float(snapshot.api_pct)


def stored_quota_ratio(summary: UsageSummary) -> float | None:
    if summary.cycle_quota_usage_ratio is not None:
        return float(summary.cycle_quota_usage_ratio)
    if summary.quota_usage_ratio is not None:
        return float(summary.quota_usage_ratio)
    return None


def api_quota_ratio_for_period(
    summary: UsageSummary,
    snapshot: AccountQuotaSnapshot | None,
    period: str,
) -> float | None:
    live = live_api_pct_for_period(snapshot, period)
    if live is not None:
        return live
    return stored_quota_ratio(summary)
