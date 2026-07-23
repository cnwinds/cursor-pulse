from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date

from pulse.pricing.estimator import aggregate_cursor_billing
from pulse.storage.models import AiAccount, AiPlan, UsageRecord
from pulse.tool_center.billing_cycle import billing_cycle_for_period


def model_family(model_name: str) -> str:
    name = (model_name or "").lower()
    if "claude" in name:
        return "Claude"
    if any(token in name for token in ("gpt", "o1", "o3", "codex")):
        return "GPT"
    if "gemini" in name:
        return "Gemini"
    if "glm" in name:
        return "GLM"
    if "minimax" in name:
        return "MiniMax"
    return "Other"


def aggregate_cost_and_models(records: list[UsageRecord]) -> tuple[float, dict[str, float]]:
    total = 0.0
    by_family: dict[str, float] = defaultdict(float)
    for rec in records:
        cost = float(rec.cost_usd or 0)
        total += cost
        by_family[model_family(rec.model)] += cost
    return total, dict(sorted(by_family.items(), key=lambda item: -item[1]))


def compute_quota_ratio(plan: AiPlan, primary_metric_value: float) -> float | None:
    if not plan.quota_ratio_enabled or not plan.quota_denominator:
        return None
    denominator = float(plan.quota_denominator)
    if denominator <= 0:
        return None
    return round(primary_metric_value / denominator * 100.0, 2)


def build_usage_summary(
    *,
    plan: AiPlan,
    records: list[UsageRecord],
    currency_unit: str | None = None,
) -> dict:
    unit = currency_unit or plan.price_currency
    unit = unit.lower() if unit else "usd"

    if plan.billing_type == "fixed_monthly_pool" and records:
        billing = aggregate_cursor_billing(records)
        cursor_pools = billing["cursor_pools"]
        api_pool = dict(cursor_pools["api"])
        third_party_pool = dict(cursor_pools.get("third_party") or {})
        api_spend = api_pool["spend_usd"] + third_party_pool.get("spend_usd", 0)
        ratio = compute_quota_ratio(plan, api_spend)
        if ratio is not None:
            api_pool["usage_ratio"] = ratio
        if plan.quota_denominator:
            api_pool["quota_usd"] = float(plan.quota_denominator)
        cursor_pools = {
            "auto_composer": cursor_pools["auto_composer"],
            "api": api_pool,
            "third_party": third_party_pool,
        }
        api_breakdown = dict(api_pool.get("breakdown_by_model") or {})
        for name, amount in (third_party_pool.get("breakdown_by_model") or {}).items():
            api_breakdown[name] = api_breakdown.get(name, 0.0) + amount
        return {
            "primary_metric_value": api_spend,
            "primary_metric_unit": unit,
            "reported_spend_usd": billing["reported_spend_usd"],
            "estimated_included_spend_usd": billing["estimated_included_spend_usd"],
            "quota_usage_ratio": ratio,
            "breakdown_by_model": api_breakdown,
            "cursor_pools": cursor_pools,
            "external_models": billing["external_models"],
            "excluded_event_count": billing["excluded_event_count"],
            "estimation_coverage_pct": billing["estimation_coverage_pct"],
            "unmatched_models": billing["unmatched_models"],
        }

    total, breakdown = aggregate_cost_and_models(records)
    ratio = compute_quota_ratio(plan, total)
    return {
        "primary_metric_value": round(total, 4),
        "primary_metric_unit": unit,
        "reported_spend_usd": round(total, 4),
        "estimated_included_spend_usd": 0.0,
        "quota_usage_ratio": ratio,
        "breakdown_by_model": breakdown,
        "estimation_coverage_pct": None,
        "unmatched_models": [],
    }


def build_account_usage_summary(
    *,
    account: AiAccount,
    plan: AiPlan,
    records: list[UsageRecord],
    period: str,
    plan_at_date: Callable[[date], AiPlan | None] | None = None,
) -> dict:
    """自然月全量 + 订阅周期额度（有 usage_resets_on 时）。"""
    base = build_usage_summary(plan=plan, records=records)

    if not account.usage_resets_on:
        return base

    cycle_start, cycle_end = billing_cycle_for_period(account.usage_resets_on, period)
    cycle_records = [
        r
        for r in records
        if r.event_date is not None and cycle_start <= r.event_date < cycle_end
    ]

    resolver = plan_at_date or (lambda _d: plan)
    cycle_plan = resolver(cycle_start) or plan
    cycle_summary = build_usage_summary(plan=cycle_plan, records=cycle_records)

    denominator = (
        float(cycle_plan.quota_denominator) if cycle_plan.quota_denominator is not None else None
    )
    result = dict(base)
    result["billing_cycle_start"] = cycle_start
    result["billing_cycle_end"] = cycle_end
    result["plan_id_used"] = cycle_plan.id
    result["quota_denominator_snapshot"] = denominator
    result["cycle_metric_value"] = cycle_summary["primary_metric_value"]
    result["cycle_quota_usage_ratio"] = cycle_summary.get("quota_usage_ratio")
    result["quota_usage_ratio"] = cycle_summary.get("quota_usage_ratio")
    return result


def infer_metric_unit_for_plan(plan: AiPlan) -> str:
    billing = (plan.billing_type or "").lower()
    quota = plan.included_quota or {}
    if billing == "rolling_window":
        return "messages"
    if "mcp_calls_per_month" in quota:
        return "calls"
    if "estimated_calls_per_month" in quota:
        return "calls"
    if (plan.price_currency or "").upper() == "CNY":
        return "cny"
    return "usd"


def _optional_quota_denominator(plan: AiPlan, unit: str) -> float | None:
    if plan.quota_denominator:
        return float(plan.quota_denominator)
    quota = plan.included_quota or {}
    if unit == "calls":
        if quota.get("mcp_calls_per_month"):
            return float(quota["mcp_calls_per_month"])
        if quota.get("estimated_calls_per_month"):
            return float(quota["estimated_calls_per_month"])
    if unit in ("usd", "cny") and plan.quota_denominator:
        return float(plan.quota_denominator)
    return None


def build_manual_usage_summary(
    *,
    plan: AiPlan,
    metric_value: float,
    metric_unit: str,
    breakdown_by_model: dict | None = None,
) -> dict:
    unit = (metric_unit or infer_metric_unit_for_plan(plan)).lower()
    ratio = None
    if plan.quota_ratio_enabled:
        ratio = compute_quota_ratio(plan, metric_value)
    else:
        denominator = _optional_quota_denominator(plan, unit)
        if denominator and denominator > 0:
            ratio = round(metric_value / denominator * 100.0, 2)

    breakdown = breakdown_by_model
    if breakdown is None and unit in ("usd", "cny"):
        family = "GLM" if unit == "cny" else "Other"
        breakdown = {family: round(metric_value, 4)}

    return {
        "primary_metric_value": round(metric_value, 4),
        "primary_metric_unit": unit,
        "reported_spend_usd": round(metric_value, 4) if unit in ("usd", "cny") else None,
        "estimated_included_spend_usd": 0.0,
        "quota_usage_ratio": ratio,
        "breakdown_by_model": breakdown or {},
        "estimation_coverage_pct": None,
        "unmatched_models": [],
    }
