from __future__ import annotations

from datetime import date

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.periods import current_period
from pulse.storage.models import UsageDailyAggregate
from pulse.tool_center.manual import ManualUsageService
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.audit import log_admin_action


class ManualUsageBody(BaseModel):
    period: str | None = None
    metric_value: float = Field(gt=0)
    metric_unit: str | None = None
    note: str | None = None


def register_usage_routes(app, get_db, require_capability, team_repo_fn, config):
    @app.post(
        "/api/v2/accounts/{account_id}/usage/manual",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def submit_manual_usage(
        account_id: str,
        body: ManualUsageBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("accounts:write")),
    ):
        team, repo = team_repo_fn(session)
        period = body.period or current_period(config)
        svc = ManualUsageService(session, team.id)
        try:
            ingestion, account, summary = svc.submit_explicit(
                member=user.member,
                period=period,
                account_id=account_id,
                metric_value=body.metric_value,
                metric_unit=body.metric_unit,
                submit_channel="web",
                repo=repo,
                raw_text=body.note,
            )
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="usage.manual_submit",
                capability="accounts:write",
                detail=f"{account_id}:{period}",
            )
            session.commit()
            return {
                "ingestion_id": ingestion.id,
                "account_id": account.id,
                "period": period,
                "summary": summary,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v2/accounts/{account_id}/usage/daily",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_daily_usage(
        account_id: str,
        start: date = Query(..., description="起始日期 YYYY-MM-DD"),
        end: date = Query(..., description="结束日期 YYYY-MM-DD"),
        session: Session = Depends(get_db),
    ):
        if end < start:
            raise HTTPException(status_code=400, detail="end 不能早于 start")
        team, _ = team_repo_fn(session)
        tool_repo = ToolCenterRepository(session, team.id)
        account = tool_repo.get_account(account_id)
        if not account or account.team_id != team.id:
            raise HTTPException(status_code=404, detail="账号不存在")

        rows = session.scalars(
            select(UsageDailyAggregate)
            .where(
                UsageDailyAggregate.account_id == account_id,
                UsageDailyAggregate.event_date >= start,
                UsageDailyAggregate.event_date <= end,
            )
            .order_by(UsageDailyAggregate.event_date, UsageDailyAggregate.model)
        ).all()
        return [
            {
                "account_id": row.account_id,
                "event_date": row.event_date.isoformat(),
                "model": row.model,
                "event_count": row.event_count,
                "total_cost_usd": float(row.total_cost_usd),
                "tokens_input": row.tokens_input,
                "tokens_output": row.tokens_output,
                "tokens_cache_read": row.tokens_cache_read,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]
