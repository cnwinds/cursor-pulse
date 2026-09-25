"""Kimi Coding Plan quota — ported from cc-switch query_kimi."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pulse.http_clients import outbound_client
from pulse.integrations.coding_plan.types import CodingPlanQuotaResult, QuotaTier

TIER_FIVE_HOUR = "five_hour"
TIER_WEEKLY_LIMIT = "weekly_limit"

_KIMI_USAGES_URL = "https://api.kimi.com/coding/v1/usages"


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _extract_reset_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, int):
        if value <= 0:
            return None
        ms = value * 1000 if value < 1_000_000_000_000 else value
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC).isoformat()
    return None


def _utilization_from_limit_remaining(limit: float, remaining: float) -> float:
    used = max(limit - remaining, 0.0)
    if limit <= 0:
        return 0.0
    return (used / limit) * 100.0


def parse_kimi_tiers(body: dict) -> list[QuotaTier]:
    tiers: list[QuotaTier] = []

    limits = body.get("limits")
    if isinstance(limits, list):
        for limit_item in limits:
            if not isinstance(limit_item, dict):
                continue
            detail = limit_item.get("detail")
            if not isinstance(detail, dict):
                continue
            limit = _parse_float(detail.get("limit")) or 1.0
            remaining = _parse_float(detail.get("remaining")) or 0.0
            resets_at = _extract_reset_time(detail.get("resetTime"))
            tiers.append(
                QuotaTier(
                    name=TIER_FIVE_HOUR,
                    utilization_pct=_utilization_from_limit_remaining(limit, remaining),
                    resets_at=resets_at,
                )
            )

    usage = body.get("usage")
    if isinstance(usage, dict):
        limit = _parse_float(usage.get("limit")) or 1.0
        remaining = _parse_float(usage.get("remaining")) or 0.0
        resets_at = _extract_reset_time(usage.get("resetTime"))
        tiers.append(
            QuotaTier(
                name=TIER_WEEKLY_LIMIT,
                utilization_pct=_utilization_from_limit_remaining(limit, remaining),
                resets_at=resets_at,
            )
        )

    return tiers


def parse_kimi_response(body: dict) -> CodingPlanQuotaResult:
    return CodingPlanQuotaResult(plan_level=None, tiers=parse_kimi_tiers(body), extras=[])


def fetch_kimi_quota(api_key: str) -> CodingPlanQuotaResult:
    with outbound_client() as client:
        resp = client.get(
            _KIMI_USAGES_URL,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Accept": "application/json",
            },
            timeout=15.0,
        )
    if resp.status_code in (401, 403):
        raise ValueError(f"Kimi authentication failed (HTTP {resp.status_code})")
    if not resp.is_success:
        raise ValueError(f"Kimi API error (HTTP {resp.status_code}): {resp.text[:500]}")
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("Kimi response is not a JSON object")
    return parse_kimi_response(body)
