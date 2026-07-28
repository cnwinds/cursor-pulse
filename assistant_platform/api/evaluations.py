from __future__ import annotations

from pulse.util.datetime_fmt import serialize_datetime

from typing import Annotated, Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.api.actor import ActorContext, build_actor_dependency
from assistant_platform.api.prompts import _require_read
from assistant_platform.evaluation.stub import run_evaluation_stub
from assistant_platform.prompts.models import PromptReleaseRow


class CreateEvaluationRunBody(BaseModel):
    release_id: str
    limit: int = Field(default=5, ge=1, le=50)


def register_evaluation_routes(
    app,
    *,
    session_factory: sessionmaker[Session],
    require_service_token: Callable[..., None],
    service_token: str,
) -> None:
    actor_dependency = build_actor_dependency(service_token)

    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.post(
        "/api/assistant/v1/evaluations/runs",
        dependencies=[Depends(require_service_token)],
    )
    def create_evaluation_run(
        body: CreateEvaluationRunBody,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        release = session.get(PromptReleaseRow, body.release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="Release not found")

        run_row = run_evaluation_stub(session, body.release_id, limit=body.limit)
        session.commit()
        return {
            "id": run_row.id,
            "release_id": run_row.release_id,
            "status": run_row.status,
            "result": run_row.result_json,
            "created_at": serialize_datetime(run_row.created_at) if run_row.created_at else None,
        }
