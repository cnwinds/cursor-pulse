from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pulse.integrations.coding_plan.types import CodingPlanExtra
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.burn_rate import WARNING_TOTAL_PCT

TIER_LABELS = {
    "five_hour": "5 小时",
    "weekly_limit": "每周",
}


@dataclass
class CodingPlanBurnAnalysis:
    quota_progress: float
    projected_exhaustion_date: None
    exhausts_before_reset: bool
    status: str
    days_until_reset: int
    remaining_headroom_pct: float
    api_limit_usd: None


def analyze_coding_plan_burn(snapshot: AccountQuotaSnapshot, today: date | None = None) -> CodingPlanBurnAnalysis:
    today = today or date.today()
    total_pct = snapshot.total_pct if snapshot.total_pct is not None else 0.0
    q_prog = round(total_pct / 100.0, 4)
    headroom = round(max(100.0 - total_pct, 0.0), 2)
    days_until_reset = max((snapshot.cycle_end - today).days, 0)

    if total_pct >= 100:
        status = "exhausted"
    elif total_pct >= WARNING_TOTAL_PCT:
        status = "warning"
    else:
        status = "healthy"

    return CodingPlanBurnAnalysis(
        quota_progress=q_prog,
        projected_exhaustion_date=None,
        exhausts_before_reset=False,
        status=status,
        days_until_reset=days_until_reset,
        remaining_headroom_pct=headroom,
        api_limit_usd=None,
    )


def quota_tiers_for_board(snapshot: AccountQuotaSnapshot) -> list[dict]:
    extra = CodingPlanExtra.from_json(snapshot.quota_extra)
    if not extra:
        return []
    out: list[dict] = []
    for tier in extra.tiers:
        out.append(
            {
                "name": tier.name,
                "label": TIER_LABELS.get(tier.name, tier.name),
                "utilization_pct": tier.utilization_pct,
                "resets_at": tier.resets_at,
            }
        )
    for item in extra.extras:
        name = str(item.get("name") or "extra")
        out.append(
            {
                "name": name,
                "label": "MCP" if "mcp" in name.lower() else name,
                "utilization_pct": float(item.get("utilization_pct") or 0),
                "resets_at": item.get("resets_at"),
            }
        )
    return out
