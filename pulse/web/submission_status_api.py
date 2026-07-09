from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pulse.tool_center.submission_status import build_ingestion_status_payload
from pulse.web.deps import PortalUser


def register_ingestion_status_routes(app, get_db, require_capability, team_repo_fn):
    @app.get("/api/v2/submission-status")
    @app.get("/api/v2/ingestion-status")
    def ingestion_status(
        period: str = Query(..., description="账期 YYYY-MM"),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("submissions:read")),
    ):
        team, _repo = team_repo_fn(session)
        return build_ingestion_status_payload(session, team.id, period, user.member)

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


register_submission_status_routes = register_ingestion_status_routes
