from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pulse.storage.models import UsageDailyAggregate, UsageIngestion, UsageRecord


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
        .group_by(UsageRecord.event_date, UsageRecord.model)
    ).all()
    for event_date, model, cnt, cost, ti, to, tcr in rows:
        session.add(
            UsageDailyAggregate(
                account_id=account_id,
                event_date=event_date,
                model=model,
                event_count=int(cnt),
                total_cost_usd=float(cost or 0),
                tokens_input=int(ti or 0),
                tokens_output=int(to or 0),
                tokens_cache_read=int(tcr or 0),
            )
        )
