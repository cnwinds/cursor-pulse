from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pulse.periods import current_period
from pulse.tool_center.knowledge import KnowledgeService
from pulse.web.audit import log_admin_action


class KnowledgeCreateBody(BaseModel):
    raw_text: str
    period: str | None = None


class KnowledgePatchBody(BaseModel):
    status: str | None = None
    pinned: bool | None = None
    title: str | None = None


def _entry_payload(entry) -> dict:
    return {
        "id": entry.id,
        "author_member_id": entry.author_member_id,
        "author_name": entry.author_member.display_name if entry.author_member else None,
        "vendor_id": entry.vendor_id,
        "vendor_name": entry.vendor.name if entry.vendor else None,
        "period": entry.period,
        "title": entry.title,
        "body": entry.body,
        "tags": entry.tags or [],
        "source_channel": entry.source_channel,
        "status": entry.status,
        "pinned": entry.pinned,
        "created_at": entry.created_at.isoformat(),
    }


def register_knowledge_routes(app, get_db, require_capability, team_repo_fn, config):
    @app.get(
        "/api/v2/knowledge",
        dependencies=[Depends(require_capability("knowledge:read"))],
    )
    def list_knowledge(
        period: str | None = None,
        include_hidden: bool = False,
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        svc = KnowledgeService(session, team.id, config)
        rows = svc.list_entries(period=period, include_hidden=include_hidden)
        return [_entry_payload(e) for e in rows]

    @app.post(
        "/api/v2/knowledge",
        dependencies=[Depends(require_capability("knowledge:write"))],
    )
    def create_knowledge(
        body: KnowledgeCreateBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("knowledge:write")),
    ):
        team, _ = team_repo_fn(session)
        period = body.period or current_period(config)
        svc = KnowledgeService(session, team.id, config)
        try:
            entry = svc.create_from_raw(
                author=user.member,
                raw_text=body.raw_text,
                source_channel="web",
                period=period,
            )
            session.commit()
            return _entry_payload(entry)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch(
        "/api/v2/knowledge/{entry_id}",
        dependencies=[Depends(require_capability("knowledge:write"))],
    )
    def patch_knowledge(
        entry_id: str,
        body: KnowledgePatchBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("knowledge:write")),
    ):
        team, _ = team_repo_fn(session)
        svc = KnowledgeService(session, team.id, config)
        try:
            fields = body.model_dump(exclude_unset=True)
            entry = svc.update_entry(entry_id, **fields)
            session.commit()
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="knowledge.update",
                capability="knowledge:write",
                detail=entry_id,
            )
            session.commit()
            return _entry_payload(entry)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v2/knowledge/digest/{period}",
        dependencies=[Depends(require_capability("knowledge:read"))],
    )
    def preview_digest(period: str, session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        svc = KnowledgeService(session, team.id, config)
        return {"text": svc.build_monthly_digest(period)}

    @app.post(
        "/api/v2/knowledge/digest/{period}/publish",
        dependencies=[Depends(require_capability("reports:publish"))],
    )
    def publish_digest(
        period: str,
        session: Session = Depends(get_db),
        user=Depends(require_capability("reports:publish")),
    ):
        team, _ = team_repo_fn(session)
        svc = KnowledgeService(session, team.id, config)
        text = svc.build_monthly_digest(period)
        if not text:
            raise HTTPException(status_code=400, detail="该账期暂无心得可发布")
        from pulse.bot.base import create_messenger

        messenger = create_messenger(config)
        messenger.send_group_text(text)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="knowledge.publish_digest",
            capability="reports:publish",
            detail=period,
        )
        session.commit()
        return {"ok": True, "text": text}
