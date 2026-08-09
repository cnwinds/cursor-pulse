from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TypedDict

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from pulse.pricing.billing_scope import kind_family
from pulse.storage.models import UsageDailyAggregate, UsageIngestion, UsageRecord


class _DailyBucket(TypedDict):
    event_date: date
    model: str | None
    kind_family: str
    event_count: int
    total_cost_usd: float
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int


def rebuild_daily_aggregates(session: Session, account_id: str, dates: set[date]) -> None:
    if not dates:
        return
    for d in dates:
        session.execute(
            delete(UsageDailyAggregate).where(
                UsageDailyAggregate.account_id == account_id,
                UsageDailyAggregate.event_date == d,
            )
        )
    rows = session.execute(
        select(
            UsageRecord.event_date,
            UsageRecord.model,
            UsageRecord.kind,
            func.count(),
            func.sum(UsageRecord.cost_usd),
            func.sum(UsageRecord.tokens_input_no_cache + UsageRecord.tokens_input_cache_write),
            func.sum(UsageRecord.tokens_output),
            func.sum(UsageRecord.tokens_cache_read),
        )
        .join(UsageIngestion, UsageRecord.ingestion_id == UsageIngestion.id)
        .where(
            UsageIngestion.account_id == account_id,
            UsageIngestion.status == "confirmed",
            UsageRecord.event_date.in_(dates),
        )
        .group_by(UsageRecord.event_date, UsageRecord.model, UsageRecord.kind)
    ).all()
    merged: dict[tuple, _DailyBucket] = {}
    for event_date, model, kind, cnt, cost, ti, to, tcr in rows:
        family = kind_family(kind)
        key = (event_date, model or "", family)
        bucket = merged.get(key)
        if bucket is None:
            merged[key] = {
                "event_date": event_date,
                "model": model,
                "kind_family": family,
                "event_count": int(cnt or 0),
                "total_cost_usd": float(cost or 0),
                "tokens_input": int(ti or 0),
                "tokens_output": int(to or 0),
                "tokens_cache_read": int(tcr or 0),
            }
            continue
        bucket["event_count"] += int(cnt or 0)
        bucket["total_cost_usd"] += float(cost or 0)
        bucket["tokens_input"] += int(ti or 0)
        bucket["tokens_output"] += int(to or 0)
        bucket["tokens_cache_read"] += int(tcr or 0)
    for bucket in merged.values():
        session.add(
            UsageDailyAggregate(
                account_id=account_id,
                event_date=bucket["event_date"],
                model=bucket["model"],
                kind_family=bucket["kind_family"],
                event_count=bucket["event_count"],
                total_cost_usd=bucket["total_cost_usd"],
                tokens_input=bucket["tokens_input"],
                tokens_output=bucket["tokens_output"],
                tokens_cache_read=bucket["tokens_cache_read"],
            )
        )


def backfill_unknown_daily_kind_families(session: Session) -> int:
    """Rebuild unknown daily-agg rows when UsageRecord.kind is available.

    Skip dates whose records have empty kind so startup does not loop.
    """
    unknown_pairs = session.execute(
        select(UsageDailyAggregate.account_id, UsageDailyAggregate.event_date)
        .where(
            or_(
                UsageDailyAggregate.kind_family == "unknown",
                UsageDailyAggregate.kind_family == "",
                UsageDailyAggregate.kind_family.is_(None),
            )
        )
        .distinct()
    ).all()
    if not unknown_pairs:
        return 0
    unknown_set = {(account_id, event_date) for account_id, event_date in unknown_pairs}
    account_ids = {account_id for account_id, _ in unknown_set}
    dates = {event_date for _, event_date in unknown_set}
    typed = session.execute(
        select(UsageIngestion.account_id, UsageRecord.event_date)
        .join(UsageRecord, UsageRecord.ingestion_id == UsageIngestion.id)
        .where(
            UsageIngestion.status == "confirmed",
            UsageIngestion.account_id.in_(account_ids),
            UsageRecord.event_date.in_(dates),
            UsageRecord.kind.is_not(None),
            UsageRecord.kind != "",
        )
        .distinct()
    ).all()
    by_account: dict[str, set[date]] = defaultdict(set)
    for account_id, event_date in typed:
        if account_id and (account_id, event_date) in unknown_set:
            by_account[str(account_id)].add(event_date)
    for account_id, day_set in by_account.items():
        rebuild_daily_aggregates(session, account_id, day_set)
    return len(by_account)
