from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.domain import CostRaw, UsageEventRecord
from pulse.pricing.cursor_tables import get_cursor_pricing_table
from pulse.pricing.estimator import resolve_cost_fields
from pulse.storage.models import UsageIngestion, UsageRecord
from pulse.tool_center.repository import ToolCenterRepository


def _record_to_event(rec: UsageRecord) -> UsageEventRecord:
    try:
        cost_raw = CostRaw(rec.cost_raw)
    except ValueError:
        cost_raw = CostRaw.NONE
    return UsageEventRecord(
        event_at=rec.event_at,
        event_date=rec.event_date,
        kind=rec.kind,
        model=rec.model,
        max_mode=rec.max_mode,
        tokens_input_cache_write=rec.tokens_input_cache_write,
        tokens_input_no_cache=rec.tokens_input_no_cache,
        tokens_cache_read=rec.tokens_cache_read,
        tokens_output=rec.tokens_output,
        tokens_total=rec.tokens_total,
        cost_raw=cost_raw,
        cost_usd=Decimal(str(rec.cost_usd)),
        cloud_agent_id=rec.cloud_agent_id,
        automation_id=rec.automation_id,
        source_row_hash=rec.source_row_hash,
    )


def reprice_ingestion(session: Session, ingestion_id: str, *, team_id: str) -> dict | None:
    ing = session.get(UsageIngestion, ingestion_id)
    if not ing or ing.status != "confirmed":
        return None

    records = list(
        session.scalars(select(UsageRecord).where(UsageRecord.ingestion_id == ingestion_id))
    )
    if not records:
        return None

    pricing_table = get_cursor_pricing_table(session=session, team_id=team_id)
    for rec in records:
        costs = resolve_cost_fields(_record_to_event(rec), table=pricing_table)
        rec.cost_usd = costs["cost_usd"]
        rec.cost_estimated_usd = costs["cost_estimated_usd"]
        rec.cost_basis = costs["cost_basis"]
        rec.pricing_version = costs["pricing_version"]
        rec.pricing_rule = costs["pricing_rule"]

    session.flush()

    if not ing.account_id:
        return {"ingestion_id": ingestion_id, "records": len(records), "summary": None}

    tool_repo = ToolCenterRepository(session, team_id)
    account = tool_repo.get_account(ing.account_id)
    if not account:
        return {"ingestion_id": ingestion_id, "records": len(records), "summary": None}

    summary = tool_repo.build_summary_for_account(account, records, ing.billing_period)
    tool_repo.upsert_usage_summary(
        account_id=ing.account_id,
        period=ing.billing_period,
        ingestion_id=ing.id,
        submitted_by_member_id=ing.member_id or "",
        summary=summary,
        shared_note=account.shared_note,
    )
    session.flush()
    return {"ingestion_id": ingestion_id, "records": len(records), "summary": summary}


def reprice_period(
    session: Session,
    *,
    team_id: str,
    period: str,
    account_id: str | None = None,
) -> list[dict]:
    query = select(UsageIngestion).where(
        UsageIngestion.billing_period == period,
        UsageIngestion.status == "confirmed",
    )
    if account_id:
        query = query.where(UsageIngestion.account_id == account_id)

    ingestions = session.scalars(query).all()
    results: list[dict] = []
    for ing in ingestions:
        result = reprice_ingestion(session, ing.id, team_id=team_id)
        if result:
            results.append(result)
    return results
