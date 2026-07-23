from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Callable

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.api.sessions import ActorContext, _actor_dependency
from assistant_platform.profiles.compiler import compile_and_persist_effective_profile
from assistant_platform.profiles.models import ProfileCorrectionRow, ProfileSignalRow


class ProfileCorrectionBody(BaseModel):
    user_id: str
    team_id: str
    signal_id: str
    correction_text: str


def _signal_json(row: ProfileSignalRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "team_id": row.team_id,
        "kind": row.kind,
        "content": row.content,
        "confidence": row.confidence,
        "source_session_ids": row.source_session_ids_json,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def register_profile_routes(
    app,
    *,
    session_factory: sessionmaker[Session],
    require_service_token: Callable[..., None],
) -> None:
    actor_dependency = _actor_dependency()

    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _ensure_self_access(actor: ActorContext, user_id: str) -> None:
        if actor.channel_user_id and actor.channel_user_id != user_id:
            raise HTTPException(status_code=403, detail="只能访问自己的画像信号")

    @app.get(
        "/api/assistant/v1/profiles/me",
        dependencies=[Depends(require_service_token)],
    )
    def get_my_profile(
        user_id: str = Query(...),
        team_id: str = Query(...),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _ensure_self_access(actor, user_id)
        now = datetime.now(timezone.utc)
        rows = session.scalars(
            select(ProfileSignalRow)
            .where(
                ProfileSignalRow.user_id == user_id,
                ProfileSignalRow.team_id == team_id,
            )
            .order_by(ProfileSignalRow.created_at.desc())
        ).all()

        def _is_active(row: ProfileSignalRow) -> bool:
            if row.expires_at is None:
                return True
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at > now

        active = [row for row in rows if _is_active(row)]
        return {
            "user_id": user_id,
            "team_id": team_id,
            "signals": [_signal_json(row) for row in active],
        }

    @app.post(
        "/api/assistant/v1/profiles/corrections",
        dependencies=[Depends(require_service_token)],
    )
    def create_profile_correction(
        body: ProfileCorrectionBody,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _ensure_self_access(actor, body.user_id)
        signal = session.get(ProfileSignalRow, body.signal_id)
        if signal is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        if signal.user_id != body.user_id or signal.team_id != body.team_id:
            raise HTTPException(status_code=403, detail="信号不属于该用户")

        correction = ProfileCorrectionRow(
            user_id=body.user_id,
            team_id=body.team_id,
            signal_id=body.signal_id,
            dimension=signal.dimension or "",
            correction_text=body.correction_text.strip(),
        )
        session.add(correction)
        session.flush()
        guidance = compile_and_persist_effective_profile(
            session,
            user_id=body.user_id,
            team_id=body.team_id,
        )
        session.commit()
        session.refresh(correction)
        return {
            "id": correction.id,
            "signal_id": correction.signal_id,
            "correction_text": correction.correction_text,
            "created_at": correction.created_at.isoformat() if correction.created_at else None,
            "effective_profile": guidance.model_dump(mode="json"),
        }
