from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.periods import current_period
from pulse.tool_center.manual import ManualUsageService
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
