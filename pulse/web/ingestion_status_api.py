from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from pulse.tool_center.ingestion_status import build_ingestion_status_payload
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


register_submission_status_routes = register_ingestion_status_routes
