"""Snapshot Headroom rules shared by Credential Pool Intake and Go proxy."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pulse.storage.models import AccountQuotaSnapshot, UsageSummary
from pulse.tool_center.billing_cycle import period_first_day, period_last_day

QuotaPoolKind = Literal["auto", "api", "unknown"]

_AUTO_POOL_ALIASES = frozenset({"auto", "auto_composer", "default", "composer"})
_API_POOL_ALIASES = frozenset({"api"})


def normalize_quota_pool(pool: str | None) -> QuotaPoolKind | None:
    """Map UI / query aliases to a Quota Pool kind.

    ``None`` means combined / unknown (use total burn). ``default`` follows
    Cursor Auto + Composer. Returns ``None`` for empty or unrecognized values
    so callers can fall back to combined scoring.
    """
    if pool is None:
        return None
    text = str(pool).strip().lower()
    if not text or text in {"unknown", "total", "combined", "all"}:
        return None
    if text in _AUTO_POOL_ALIASES:
        return "auto"
    if text in _API_POOL_ALIASES:
        return "api"
    return None


def quota_pool_for_model(model: str | None) -> QuotaPoolKind:
    """Match Go ``quotaPoolForModel``: Auto vs API vs unknown (BYOK)."""
    from pulse.pricing.billing_scope import is_auto_composer_model, is_likely_byok_model

    if not (model or "").strip():
        return "unknown"
    if is_auto_composer_model(model):
        return "auto"
    if is_likely_byok_model(model):
        return "unknown"
    return "api"


def resolve_quota_pool(*, quota_pool: str | None = None, model: str | None = None) -> QuotaPoolKind | None:
    """Prefer an explicit Quota Pool; otherwise infer from the billed model."""
    normalized = normalize_quota_pool(quota_pool)
    if normalized is not None:
        return normalized
    if model:
        kind = quota_pool_for_model(model)
        if kind in ("auto", "api"):
            return kind
    return None


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
