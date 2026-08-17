from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TypedDict

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from pulse.pricing.billing_scope import kind_family
from pulse.pricing.estimator import effective_pool_cost
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
    # Row-level fold so total_cost_usd matches UsageSummary (effective_pool_cost:
    # reported cost_usd, else cost_estimated_usd for included pool spend).
    records = session.scalars(
        select(UsageRecord)
        .join(UsageIngestion, UsageRecord.ingestion_id == UsageIngestion.id)
        .where(
            UsageIngestion.account_id == account_id,
            UsageIngestion.status == "confirmed",
            UsageRecord.event_date.in_(dates),
        )
    ).all()
    merged: dict[tuple, _DailyBucket] = {}
    for rec in records:
        family = kind_family(rec.kind)
        key = (rec.event_date, rec.model or "", family)
        bucket = merged.get(key)
        cost = float(effective_pool_cost(rec) or 0)
        ti = int((rec.tokens_input_no_cache or 0) + (rec.tokens_input_cache_write or 0))
        to = int(rec.tokens_output or 0)
        tcr = int(rec.tokens_cache_read or 0)
        if bucket is None:
            merged[key] = {
                "event_date": rec.event_date,
                "model": rec.model,
                "kind_family": family,
                "event_count": 1,
                "total_cost_usd": cost,
                "tokens_input": ti,
                "tokens_output": to,
                "tokens_cache_read": tcr,
            }
            continue
        bucket["event_count"] += 1
        bucket["total_cost_usd"] += cost
        bucket["tokens_input"] += ti
        bucket["tokens_output"] += to
        bucket["tokens_cache_read"] += tcr
    for bucket in merged.values():
        session.add(
            UsageDailyAggregate(
                account_id=account_id,
                event_date=bucket["event_date"],
                model=bucket["model"],
                kind_family=bucket["kind_family"],
                event_count=bucket["event_count"],
                total_cost_usd=round(bucket["total_cost_usd"], 4),
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
