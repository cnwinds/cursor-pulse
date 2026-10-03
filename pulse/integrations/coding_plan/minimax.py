"""MiniMax Coding Plan quota — ported from cc-switch parse_minimax_tiers."""

from __future__ import annotations

from datetime import UTC, datetime

from pulse.http_clients import outbound_client
from pulse.integrations.coding_plan.types import CodingPlanQuotaResult, QuotaTier

TIER_FIVE_HOUR = "five_hour"
TIER_WEEKLY_LIMIT = "weekly_limit"


def _millis_to_iso8601(ms: int) -> str | None:
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC).isoformat()


def parse_minimax_tiers(body: dict) -> list[QuotaTier]:
    tiers: list[QuotaTier] = []
    model_remains = body.get("model_remains")
    if not isinstance(model_remains, list):
        return tiers

    item = None
    for row in model_remains:
        if isinstance(row, dict) and row.get("model_name") == "general":
            item = row
            break
    if item is None:
        return tiers

    remain_interval = item.get("current_interval_remaining_percent")
    if remain_interval is not None:
        remain_f = float(remain_interval)
        end_time = item.get("end_time")
        resets_at = _millis_to_iso8601(int(end_time)) if end_time is not None else None
        tiers.append(
            QuotaTier(
                name=TIER_FIVE_HOUR,
                utilization_pct=100.0 - remain_f,
                resets_at=resets_at,
            )
        )

    if item.get("current_weekly_status") == 1:
        remain_weekly = item.get("current_weekly_remaining_percent")
        if remain_weekly is not None:
            remain_w = float(remain_weekly)
            weekly_end = item.get("weekly_end_time")
            resets_at = _millis_to_iso8601(int(weekly_end)) if weekly_end is not None else None
            tiers.append(
                QuotaTier(
                    name=TIER_WEEKLY_LIMIT,
                    utilization_pct=100.0 - remain_w,
                    resets_at=resets_at,
                )
            )
    return tiers


def parse_minimax_response(body: dict) -> CodingPlanQuotaResult:
    base_resp = body.get("base_resp") or {}
    status_code = base_resp.get("status_code", -1)
    if status_code != 0:
        msg = base_resp.get("status_msg") or "Unknown error"
        raise ValueError(f"MiniMax API error (code {status_code}): {msg}")
    return CodingPlanQuotaResult(plan_level=None, tiers=parse_minimax_tiers(body))


def minimax_api_domain(region: str) -> str:
    return "api.minimax.io" if region == "global" else "api.minimaxi.com"


def fetch_minimax_quota(api_key: str, *, region: str) -> CodingPlanQuotaResult:
    domain = minimax_api_domain(region)
    url = f"https://{domain}/v1/api/openplatform/coding_plan/remains"
    with outbound_client() as client:
        resp = client.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
    if resp.status_code in (401, 403):
        raise ValueError(f"MiniMax authentication failed (HTTP {resp.status_code})")
    if not resp.is_success:
        raise ValueError(f"MiniMax API error (HTTP {resp.status_code}): {resp.text[:500]}")
    return parse_minimax_response(resp.json())
