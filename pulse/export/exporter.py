from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import Member, UsageIngestion, UsageRecord


def export_usage_csv(session: Session, period: str, dest: Path) -> Path:
    ingestion_ids = session.scalars(
        select(UsageIngestion.id).where(
            UsageIngestion.billing_period == period,
            UsageIngestion.status == "confirmed",
        )
    ).all()
    records = list(
        session.scalars(
            select(UsageRecord).where(UsageRecord.ingestion_id.in_(ingestion_ids))
        )
    )
    members = {m.id: m.display_name for m in session.scalars(select(Member)).all()}

    dest.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "member_name", "event_date", "model", "kind", "tokens_total",
        "cost_usd", "cost_raw",
    ]
    with dest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "member_name": members.get(r.member_id, r.member_id),
                "event_date": r.event_date.isoformat(),
                "model": r.model,
                "kind": r.kind,
                "tokens_total": r.tokens_total,
                "cost_usd": r.cost_usd,
                "cost_raw": r.cost_raw,
            })
    return dest
