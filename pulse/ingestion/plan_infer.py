"""Infer Cursor catalog plan from GetCurrentPeriodUsage payload."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

from pulse.storage.models import AiPlan


def _ms_to_date(value: object) -> date | None:
    if value is None:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def cycle_end_from_period_usage(period_usage: dict | None) -> date | None:
    if not period_usage:
        return None
    return _ms_to_date(period_usage.get("billingCycleEnd"))


def _plan_pool_usd(plan: AiPlan) -> float | None:
    quota = plan.included_quota or {}
    if isinstance(quota, dict) and quota.get("spend_cap_usd") is not None:
        try:
            return float(quota["spend_cap_usd"])
        except (TypeError, ValueError):
            pass
    if plan.quota_denominator is not None:
        try:
            return float(plan.quota_denominator)
        except (TypeError, ValueError):
            pass
    return None


def infer_plan_from_period_usage(
    plans: Sequence[AiPlan],
    period_usage: dict | None,
) -> AiPlan | None:
    """Match catalog plan by planUsage.limit (cents) ≈ spend_cap_usd * 100."""
    if not plans or not period_usage:
        return None
    plan_usage = period_usage.get("planUsage") or {}
    try:
        limit_cents = int(plan_usage.get("limit") or 0)
    except (TypeError, ValueError):
        return None
    if limit_cents <= 0:
        return None
    limit_usd = limit_cents / 100.0

    exact = [
        p
        for p in plans
        if (pool := _plan_pool_usd(p)) is not None and abs(pool - limit_usd) < 0.01
    ]
    if exact:
        return sorted(exact, key=lambda p: p.slug)[0]

    # Closest pool size (e.g. Cursor changes included cents slightly).
    scored: list[tuple[float, AiPlan]] = []
    for plan in plans:
        pool = _plan_pool_usd(plan)
        if pool is None or pool <= 0:
            continue
        scored.append((abs(pool - limit_usd), plan))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1].slug))
    best_delta, best = scored[0]
    # Reject wild mismatches (e.g. $20 vs $400).
    if best_delta > max(5.0, limit_usd * 0.25):
        return None
    return best


def default_cursor_plan(plans: Sequence[AiPlan]) -> AiPlan | None:
    if not plans:
        return None
    for slug in ("pro", "pro_plus", "ultra"):
        match = next((p for p in plans if p.slug == slug), None)
        if match:
            return match
    return plans[0]
