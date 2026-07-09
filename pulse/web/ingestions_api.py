from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pulse.storage.models import Member
from pulse.storage.repository import input_type_from_source_type
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.deps import PortalUser


def _ingestion_payload(session: Session, team_id: str, ing) -> dict:
    member = session.get(Member, ing.member_id) if ing.member_id else None
    account_identifier = None
    vendor_name = None
    if ing.account_id:
        tool_repo = ToolCenterRepository(session, team_id)
        account = tool_repo.get_account(ing.account_id)
        if account:
            account_identifier = account.account_identifier
            vendor_name = account.vendor.name if account.vendor else None
    return {
        "id": ing.id,
        "id_prefix": ing.id[:8],
        "account_id": ing.account_id,
        "account_identifier": account_identifier,
        "vendor_name": vendor_name,
        "member_id": ing.member_id,
        "member_name": member.display_name if member else None,
        "period": ing.billing_period,
        "billing_period": ing.billing_period,
        "source_type": ing.source_type,
        "input_type": input_type_from_source_type(ing.source_type),
        "channel": ing.channel,
        "status": ing.status,
        "event_count": ing.event_count,
        "ingested_at": ing.ingested_at.isoformat(),
        "confirmed_at": ing.confirmed_at.isoformat() if ing.confirmed_at else None,
        "error_message": ing.error_message,
    }


def register_ingestions_routes(app, get_db, require_capability, team_repo_fn):
    @app.get(
        "/api/v2/submissions",
        dependencies=[Depends(require_capability("submissions:read"))],
    )
    @app.get(
        "/api/v2/ingestions",
        dependencies=[Depends(require_capability("submissions:read"))],
    )
    def list_ingestions(
        period: str | None = Query(None),
        status: str | None = Query(None),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("submissions:read")),
    ):
        team, repo = team_repo_fn(session)
        rows = repo.list_ingestions(period=period, status=status)
        return [_ingestion_payload(session, team.id, ing) for ing in rows]

    @app.post(
        "/api/v2/submissions/{ingestion_id}/confirm",
        dependencies=[Depends(require_capability("submissions:review"))],
    )
    @app.post(
        "/api/v2/ingestions/{ingestion_id}/confirm",
        dependencies=[Depends(require_capability("submissions:review"))],
    )
    def confirm_ingestion_route(ingestion_id: str, session: Session = Depends(get_db)):
        _team, repo = team_repo_fn(session)
        try:
            repo.confirm_ingestion(ingestion_id)
            session.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "ingestion_id": ingestion_id, "status": "confirmed"}

    @app.post(
        "/api/v2/submissions/{ingestion_id}/reject",
        dependencies=[Depends(require_capability("submissions:review"))],
    )
    @app.post(
        "/api/v2/ingestions/{ingestion_id}/reject",
        dependencies=[Depends(require_capability("submissions:review"))],
    )
    def reject_ingestion_route(ingestion_id: str, session: Session = Depends(get_db)):
        _team, repo = team_repo_fn(session)
        try:
            repo.reject_ingestion(ingestion_id)
            session.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "ingestion_id": ingestion_id, "status": "rejected"}
