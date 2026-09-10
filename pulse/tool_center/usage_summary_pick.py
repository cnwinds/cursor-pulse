"""Pick the UsageSummary row that belongs on a quota-board card.

Mirrors web-admin ``preferSummaryForBoardCycle`` / ``periodsForBoardCycle``.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pulse.storage.models import UsageSummary


def billing_cycle_date_range(
    cycle_start: str | None, cycle_end: str | None
) -> tuple[str, str] | None:
    if not cycle_start or not cycle_end:
        return None
    try:
        end = date.fromisoformat(cycle_end) - timedelta(days=1)
    except ValueError:
        return None
    return cycle_start, end.isoformat()


def periods_for_board_cycle(
    cycle_start: str | None, cycle_end: str | None
) -> list[str]:
    rng = billing_cycle_date_range(cycle_start, cycle_end)
    if not rng:
        return []
    start = date.fromisoformat(rng[0])
    last = date.fromisoformat(rng[1])
    periods: list[str] = []
    year, month = start.year, start.month
    while date(year, month, 1) <= last:
        periods.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return periods


def prefer_summary_for_board_cycle(
    current: dict | None,
    nxt: dict,
    cycle_start: str | None,
) -> dict | None:
    """Keep in sync with web-admin/src/utils/usage.ts preferSummaryForBoardCycle."""
    if cycle_start and nxt.get("billing_cycle_start") != cycle_start:
        return current
    if not current:
        return nxt
    cur_api = len(
        ((current.get("cursor_pools") or {}).get("api") or {}).get("breakdown_by_model")
        or {}
    )
    next_api = len(
        ((nxt.get("cursor_pools") or {}).get("api") or {}).get("breakdown_by_model") or {}
    )
    if next_api != cur_api:
        return nxt if next_api > cur_api else current
    return nxt if (nxt.get("period") or "") > (current.get("period") or "") else current


def usage_summary_board_payload(row: UsageSummary) -> dict:
    return {
        "account_id": row.account_id,
        "period": row.period,
        "primary_metric_value": float(row.primary_metric_value),
        "primary_metric_unit": row.primary_metric_unit,
        "reported_spend_usd": (
            float(row.reported_spend_usd) if row.reported_spend_usd is not None else None
        ),
        "estimated_included_spend_usd": (
            float(row.estimated_included_spend_usd)
            if row.estimated_included_spend_usd is not None
            else None
        ),
        "quota_usage_ratio": row.quota_usage_ratio,
        "billing_cycle_start": (
            row.billing_cycle_start.isoformat() if row.billing_cycle_start else None
        ),
        "billing_cycle_end": (
            row.billing_cycle_end.isoformat() if row.billing_cycle_end else None
        ),
        "quota_denominator_snapshot": (
            float(row.quota_denominator_snapshot)
            if row.quota_denominator_snapshot is not None
            else None
        ),
        "cycle_metric_value": (
            float(row.cycle_metric_value) if row.cycle_metric_value is not None else None
        ),
        "cycle_quota_usage_ratio": row.cycle_quota_usage_ratio,
        "breakdown_by_model": row.breakdown_by_model,
        "cursor_pools": row.cursor_pools,
        "external_models": row.external_models,
    }


def attach_board_usage_summaries(session: Session, items: list[dict]) -> None:
    """Set ``usage_summary`` on each board item (or None)."""
    account_ids = [item["account_id"] for item in items if item.get("account_id")]
    if not account_ids:
        return
    periods: set[str] = set()
    cycle_starts: set[date] = set()
    for item in items:
        periods.update(
            periods_for_board_cycle(item.get("cycle_start"), item.get("cycle_end"))
        )
        raw_start = item.get("cycle_start")
        if raw_start:
            try:
                cycle_starts.add(date.fromisoformat(raw_start))
            except ValueError:
                pass
    clauses = []
    if periods:
        clauses.append(UsageSummary.period.in_(periods))
    if cycle_starts:
        clauses.append(UsageSummary.billing_cycle_start.in_(cycle_starts))
    rows: list[UsageSummary] = []
    if clauses:
        rows = list(
            session.scalars(
                select(UsageSummary).where(
                    UsageSummary.account_id.in_(account_ids),
                    or_(*clauses),
                )
            )
        )
    by_account: dict[str, list[UsageSummary]] = {}
    for row in rows:
        by_account.setdefault(row.account_id, []).append(row)
    for item in items:
        picked = None
        for row in by_account.get(item["account_id"], []):
            picked = prefer_summary_for_board_cycle(
                picked,
                usage_summary_board_payload(row),
                item.get("cycle_start"),
            )
        item["usage_summary"] = picked
