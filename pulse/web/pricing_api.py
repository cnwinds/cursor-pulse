from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import Member
from pulse.web.audit import log_admin_action
from pulse.web.deps import PortalUser


class CursorPricingBody(BaseModel):
    vendor_slug: str = "cursor"
    version: str = Field(min_length=1, max_length=64)
    effective_from: str | None = None
    rules: list[dict] = Field(default_factory=list)
    fallback: dict


def register_pricing_routes(app, get_db, require_capability, team_repo_fn):
    @app.get(
        "/api/v2/pricing/cursor",
        dependencies=[Depends(require_capability("settings:read"))],
    )
    def get_cursor_pricing(session: Session = Depends(get_db)):
        from pulse.pricing.store import pricing_api_payload

        team, _ = team_repo_fn(session)
        rows = session.scalars(select(Member).where(Member.team_id == team.id)).all()
        names = {m.id: m.display_name for m in rows}
        return pricing_api_payload(session, team.id, member_names=names)

    @app.put(
        "/api/v2/pricing/cursor",
        dependencies=[Depends(require_capability("settings:write"))],
    )
    def put_cursor_pricing(
        body: CursorPricingBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("settings:write")),
    ):
        from pulse.pricing.store import pricing_api_payload, save_team_cursor_pricing

        team, _ = team_repo_fn(session)
        try:
            save_team_cursor_pricing(
                session,
                team_id=team.id,
                data=body.model_dump(),
                member_id=user.member.id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="pricing.cursor_update",
            capability="settings:write",
            detail=f"version={body.version} rules={len(body.rules)}",
            channel="web",
        )
        session.commit()
        names = {user.member.id: user.member.display_name}
        return pricing_api_payload(session, team.id, member_names=names)

    @app.post(
        "/api/v2/pricing/cursor/reset",
        dependencies=[Depends(require_capability("settings:write"))],
    )
    def reset_cursor_pricing(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("settings:write")),
    ):
        from pulse.pricing.store import pricing_api_payload, reset_team_cursor_pricing

        team, _ = team_repo_fn(session)
        removed = reset_team_cursor_pricing(session, team.id)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="pricing.cursor_reset",
            capability="settings:write",
            detail="removed_override" if removed else "already_builtin",
            channel="web",
        )
        session.commit()
        return pricing_api_payload(session, team.id)
