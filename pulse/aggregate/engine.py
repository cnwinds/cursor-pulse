from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.domain import COMPUTATION_VERSION
from pulse.periods import pct_change, previous_period
from pulse.storage.models import Member, MetricSnapshot, UsageIngestion, UsageRecord
from pulse.tool_center.aggregate import aggregate_account_metrics


def _rank(items: list[tuple[str, float | int]]) -> list[dict]:
    sorted_items = sorted(items, key=lambda x: x[1], reverse=True)
    return [{"member_id": mid, "value": val, "rank": idx + 1} for idx, (mid, val) in enumerate(sorted_items)]


def _records_for_period(session: Session, period: str, team_id: str | None = None) -> list[UsageRecord]:
    ingestion_query = select(UsageIngestion.id).where(
        UsageIngestion.billing_period == period,
        UsageIngestion.status == "confirmed",
    )
    if team_id:
        ingestion_query = ingestion_query.join(Member).where(Member.team_id == team_id)
    ingestion_ids = session.scalars(ingestion_query).all()
    if not ingestion_ids:
        return []
    return list(
        session.scalars(
            select(UsageRecord).where(UsageRecord.ingestion_id.in_(ingestion_ids))
        )
    )


def aggregate_period(session: Session, period: str, *, team_id: str | None = None) -> dict:
    records = _records_for_period(session, period, team_id=team_id)

    if not records:
        raise ValueError(f"No usage records for period {period}")

    member_ids = {r.member_id for r in records}
    members = {
        m.id: m.display_name
        for m in session.scalars(select(Member).where(Member.id.in_(member_ids)))
    }

    total_cost = Decimal("0")
    total_events = len(records)
    total_tokens = 0
    cost_by_member: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    events_by_member: dict[str, int] = defaultdict(int)
    tokens_by_member: dict[str, int] = defaultdict(int)
    events_by_model: dict[str, int] = defaultdict(int)
    tokens_by_model: dict[str, int] = defaultdict(int)
    cost_by_model: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    kind_distribution: dict[str, int] = defaultdict(int)

    for r in records:
        total_tokens += r.tokens_total
        events_by_member[r.member_id] += 1
        tokens_by_member[r.member_id] += r.tokens_total
        events_by_model[r.model] += 1
        tokens_by_model[r.model] += r.tokens_total
        kind_distribution[r.kind] += 1
        if r.cost_raw == "usage_based":
            cost = Decimal(str(r.cost_usd))
            total_cost += cost
            cost_by_member[r.member_id] += cost
            cost_by_model[r.model] += cost

    active_query = select(Member).where(Member.status == "active")
    if team_id:
        active_query = active_query.where(Member.team_id == team_id)
    active_members = session.scalars(active_query).all()
    submitted_ids = set(member_ids)
    unsubmitted = [m.display_name for m in active_members if m.id not in submitted_ids]
    zero_usage = [members[mid] for mid, cnt in events_by_member.items() if cnt == 0]

    metrics = {
        "period": period,
        "team_id": team_id,
        "total_cost_usd": float(total_cost),
        "total_events": total_events,
        "total_tokens": total_tokens,
        "member_count_reported": len(submitted_ids),
        "member_count_expected": len(active_members),
        "cost_per_member_usd": float(total_cost / len(submitted_ids)) if submitted_ids else 0.0,
        "events_per_member": total_events / len(submitted_ids) if submitted_ids else 0.0,
        "tokens_per_member": total_tokens / len(submitted_ids) if submitted_ids else 0.0,
        "cost_by_member": _rank([(mid, float(v)) for mid, v in cost_by_member.items()]),
        "events_by_member": _rank([(mid, v) for mid, v in events_by_member.items()]),
        "tokens_by_member": _rank([(mid, v) for mid, v in tokens_by_member.items()]),
        "events_by_model": dict(sorted(events_by_model.items(), key=lambda x: -x[1])),
        "tokens_by_model": dict(sorted(tokens_by_model.items(), key=lambda x: -x[1])),
        "cost_by_model": {k: float(v) for k, v in sorted(cost_by_model.items(), key=lambda x: -x[1])},
        "kind_distribution": dict(kind_distribution),
        "unsubmitted_members": unsubmitted,
        "zero_usage_members": zero_usage,
        "member_names": members,
    }

    prev = previous_period(period)
    try:
        prev_query = select(MetricSnapshot).where(MetricSnapshot.period == prev)
        if team_id:
            prev_query = prev_query.where(MetricSnapshot.team_id == team_id)
        prev_snap = session.scalar(prev_query.order_by(MetricSnapshot.computed_at.desc()))
        if prev_snap:
            prev_metrics = prev_snap.metrics_json
            prev_cost = prev_metrics.get("total_cost_usd", 0) or 0
            prev_events = prev_metrics.get("total_events", 0) or 0
            metrics["mom_cost_change_pct"] = pct_change(prev_cost, float(total_cost))
            metrics["mom_events_change_pct"] = pct_change(prev_events, total_events)
        else:
            metrics["mom_cost_change_pct"] = None
            metrics["mom_events_change_pct"] = None
    except Exception:
        metrics["mom_cost_change_pct"] = None
        metrics["mom_events_change_pct"] = None

    if team_id:
        account_metrics = aggregate_account_metrics(session, period, team_id=team_id)
        if account_metrics.get("account_count_active"):
            metrics["account_metrics"] = account_metrics

    snapshot = MetricSnapshot(
        team_id=team_id,
        period=period,
        snapshot_type="monthly",
        metrics_json=metrics,
        computed_at=datetime.now(timezone.utc),
        computation_version=COMPUTATION_VERSION,
    )
    session.add(snapshot)
    session.flush()
    return metrics


def _previous_period(period: str) -> str:
    """Deprecated: use pulse.periods.previous_period."""
    return previous_period(period)


def _pct_change(old: float | int, new: float | int) -> float | None:
    """Deprecated: use pulse.periods.pct_change."""
    return pct_change(old, new)
