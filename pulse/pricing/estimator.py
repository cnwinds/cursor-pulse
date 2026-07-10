from __future__ import annotations

from collections import defaultdict
from datetime import date

from pulse.domain import CostRaw, UsageEventRecord
from pulse.pricing.billing_scope import classify_billing_scope
from pulse.pricing.cursor_tables import get_cursor_pricing_table
from pulse.pricing.types import CostEstimate, PricingTable, estimate_token_cost
from pulse.storage.models import UsageRecord


def _needs_estimation(cost_raw: CostRaw | str) -> bool:
    raw = cost_raw.value if isinstance(cost_raw, CostRaw) else str(cost_raw)
    return raw in {CostRaw.INCLUDED.value, CostRaw.FREE.value}


def estimate_event_record(
    rec: UsageEventRecord,
    *,
    table: PricingTable | None = None,
) -> CostEstimate | None:
    table = table or get_cursor_pricing_table(rec.event_date)
    scope = classify_billing_scope(kind=rec.kind, model=rec.model)
    if scope in ("external", "excluded"):
        return None

    if rec.cost_raw in (CostRaw.INCLUDED, CostRaw.FREE):
        if scope == "auto_composer":
            model_name = rec.model if (rec.model or "").lower().startswith("composer") else "auto"
            return estimate_token_cost(
                model=model_name,
                max_mode=rec.max_mode,
                tokens_input_no_cache=rec.tokens_input_no_cache,
                tokens_input_cache_write=rec.tokens_input_cache_write,
                tokens_cache_read=rec.tokens_cache_read,
                tokens_output=rec.tokens_output,
                table=table,
                pricing_rule_label="included:auto_composer",
                confidence=0.9,
            )
        return estimate_token_cost(
            model=rec.model,
            max_mode=rec.max_mode,
            tokens_input_no_cache=rec.tokens_input_no_cache,
            tokens_input_cache_write=rec.tokens_input_cache_write,
            tokens_cache_read=rec.tokens_cache_read,
            tokens_output=rec.tokens_output,
            table=table,
            pricing_rule_label="included:api",
            confidence=0.85,
        )

    return estimate_token_cost(
        model=rec.model,
        max_mode=rec.max_mode,
        tokens_input_no_cache=rec.tokens_input_no_cache,
        tokens_input_cache_write=rec.tokens_input_cache_write,
        tokens_cache_read=rec.tokens_cache_read,
        tokens_output=rec.tokens_output,
        table=table,
    )


def resolve_cost_fields(
    rec: UsageEventRecord,
    *,
    table: PricingTable | None = None,
) -> dict:
    """Return cost_usd, cost_estimated_usd, cost_basis, pricing_version, pricing_rule."""
    scope = classify_billing_scope(kind=rec.kind, model=rec.model)
    if scope == "external":
        return {
            "cost_usd": 0.0,
            "cost_estimated_usd": 0.0,
            "cost_basis": "external",
            "pricing_version": None,
            "pricing_rule": None,
        }
    if scope == "excluded":
        return {
            "cost_usd": 0.0,
            "cost_estimated_usd": 0.0,
            "cost_basis": "excluded",
            "pricing_version": None,
            "pricing_rule": None,
        }

    reported = float(rec.cost_usd or 0)
    cost_raw = rec.cost_raw

    if cost_raw == CostRaw.USAGE_BASED and reported > 0:
        return {
            "cost_usd": reported,
            "cost_estimated_usd": 0.0,
            "cost_basis": "reported",
            "pricing_version": None,
            "pricing_rule": None,
        }

    if cost_raw == CostRaw.USAGE_BASED and reported == 0:
        estimate = estimate_event_record(rec, table=table)
        if estimate and estimate.cost_usd > 0:
            return {
                "cost_usd": 0.0,
                "cost_estimated_usd": estimate.cost_usd,
                "cost_basis": "estimated",
                "pricing_version": estimate.pricing_version,
                "pricing_rule": estimate.pricing_rule,
            }
        return {
            "cost_usd": 0.0,
            "cost_estimated_usd": 0.0,
            "cost_basis": "none",
            "pricing_version": None,
            "pricing_rule": None,
        }

    if _needs_estimation(cost_raw):
        estimate = estimate_event_record(rec, table=table)
        if estimate:
            return {
                "cost_usd": 0.0,
                "cost_estimated_usd": estimate.cost_usd,
                "cost_basis": "estimated",
                "pricing_version": estimate.pricing_version,
                "pricing_rule": estimate.pricing_rule,
            }
        return {
            "cost_usd": 0.0,
            "cost_estimated_usd": 0.0,
            "cost_basis": "none",
            "pricing_version": None,
            "pricing_rule": None,
        }

    return {
        "cost_usd": reported,
        "cost_estimated_usd": 0.0,
        "cost_basis": "none",
        "pricing_version": None,
        "pricing_rule": None,
    }


def effective_pool_cost(record: UsageRecord) -> float:
    reported = float(record.cost_usd or 0)
    if record.cost_basis == "reported" and reported > 0:
        return reported
    estimated = float(record.cost_estimated_usd or 0)
    if record.cost_basis == "estimated" and estimated > 0:
        return estimated
    if reported > 0:
        return reported
    return estimated


def _empty_pool_bucket() -> dict:
    return {
        "spend_usd": 0.0,
        "reported_spend_usd": 0.0,
        "estimated_spend_usd": 0.0,
        "breakdown_by_model": {},
    }


def _round_pool_bucket(bucket: dict) -> dict:
    breakdown = dict(
        sorted(
            ((k, round(v, 4)) for k, v in bucket["breakdown_by_model"].items()),
            key=lambda item: -item[1],
        )
    )
    return {
        "spend_usd": round(bucket["spend_usd"], 4),
        "reported_spend_usd": round(bucket["reported_spend_usd"], 4),
        "estimated_spend_usd": round(bucket["estimated_spend_usd"], 4),
        "breakdown_by_model": breakdown,
    }


def aggregate_cursor_billing(records: list[UsageRecord]) -> dict:
    """Split Cursor usage into auto+composer pool, API pool, and external BYOK."""
    pools = {
        "auto_composer": _empty_pool_bucket(),
        "api": _empty_pool_bucket(),
    }
    external_models: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total_tokens": 0, "event_count": 0}
    )
    excluded_event_count = 0
    included_total = 0
    estimated_rows = 0
    unmatched_models: set[str] = set()

    for rec in records:
        scope = classify_billing_scope(kind=rec.kind, model=rec.model)
        model_name = (rec.model or "unknown").strip() or "unknown"

        if scope == "excluded":
            excluded_event_count += 1
            continue
        if scope == "external":
            external_models[model_name]["total_tokens"] += int(rec.tokens_total or 0)
            external_models[model_name]["event_count"] += 1
            continue

        bucket = pools[scope]
        cost = effective_pool_cost(rec)
        bucket["spend_usd"] += cost
        bucket["breakdown_by_model"][model_name] = (
            bucket["breakdown_by_model"].get(model_name, 0.0) + cost
        )

        reported = float(rec.cost_usd or 0)
        if rec.cost_basis == "reported" and reported > 0:
            bucket["reported_spend_usd"] += reported
        elif rec.cost_basis == "estimated":
            est = float(rec.cost_estimated_usd or 0)
            bucket["estimated_spend_usd"] += est
            if _needs_estimation(rec.cost_raw):
                included_total += 1
                if est > 0:
                    estimated_rows += 1
                else:
                    unmatched_models.add(rec.model)

    cursor_pools = {
        "auto_composer": _round_pool_bucket(pools["auto_composer"]),
        "api": _round_pool_bucket(pools["api"]),
    }
    reported_spend = cursor_pools["auto_composer"]["reported_spend_usd"] + cursor_pools["api"][
        "reported_spend_usd"
    ]
    estimated_included_spend = cursor_pools["auto_composer"]["estimated_spend_usd"] + cursor_pools[
        "api"
    ]["estimated_spend_usd"]
    pool_spend = cursor_pools["auto_composer"]["spend_usd"] + cursor_pools["api"]["spend_usd"]

    coverage = None
    if included_total:
        coverage = round(estimated_rows / included_total * 100.0, 2)

    external_sorted = dict(
        sorted(
            external_models.items(),
            key=lambda item: -item[1]["total_tokens"],
        )
    )

    return {
        "cursor_pools": cursor_pools,
        "external_models": external_sorted,
        "excluded_event_count": excluded_event_count,
        "reported_spend_usd": round(reported_spend, 4),
        "estimated_included_spend_usd": round(estimated_included_spend, 4),
        "pool_spend_usd": round(pool_spend, 4),
        "estimation_coverage_pct": coverage,
        "unmatched_models": sorted(unmatched_models),
        "breakdown_pool_by_model": cursor_pools["api"]["breakdown_by_model"],
    }


def aggregate_pool_costs(records: list[UsageRecord]) -> dict:
    """Backward-compatible wrapper around dual-pool aggregation."""
    return aggregate_cursor_billing(records)
