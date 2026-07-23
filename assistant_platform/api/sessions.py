from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Callable

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_store import close_session
from assistant_platform.memory.deletion import purge_session_memory
from assistant_platform.storage.repository import AssistantRepository


class CloseSessionBody(BaseModel):
    reason: str = "manual"


class ActorContext(BaseModel):
    member_id: str = ""
    role: str = ""
    channel_user_id: str = ""
    permissions: set[str] = Field(default_factory=set)


def _parse_permissions(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _actor_dependency():
    def dependency(
        x_pulse_actor_member_id: Annotated[str | None, Header(alias="X-Pulse-Actor-Member-Id")] = None,
        x_pulse_actor_role: Annotated[str | None, Header(alias="X-Pulse-Actor-Role")] = None,
        x_pulse_actor_channel_user_id: Annotated[
            str | None, Header(alias="X-Pulse-Actor-Channel-User-Id")
        ] = None,
        x_pulse_actor_permissions: Annotated[
            str | None, Header(alias="X-Pulse-Actor-Permissions")
        ] = None,
    ) -> ActorContext:
        return ActorContext(
            member_id=(x_pulse_actor_member_id or "").strip(),
            role=(x_pulse_actor_role or "").strip(),
            channel_user_id=(x_pulse_actor_channel_user_id or "").strip(),
            permissions=_parse_permissions(x_pulse_actor_permissions),
        )

    return dependency


def _has_permission(actor: ActorContext, permission: str) -> bool:
    return permission in actor.permissions


def _require_read(actor: ActorContext) -> None:
    if _has_permission(actor, "assistant:sessions:read:all"):
        return
    if _has_permission(actor, "assistant:sessions:read:self"):
        return
    raise HTTPException(status_code=403, detail="缺少 assistant:sessions:read 权限")


def _require_export(actor: ActorContext) -> None:
    if _has_permission(actor, "assistant:sessions:export:all"):
        return
    if _has_permission(actor, "assistant:sessions:export:self"):
        return
    raise HTTPException(status_code=403, detail="缺少 assistant:sessions:export 权限")


def _can_access_session(actor: ActorContext, session_row: ChatSessionRow) -> bool:
    if _has_permission(actor, "assistant:sessions:read:all"):
        return True
    if not _has_permission(actor, "assistant:sessions:read:self"):
        return False
    if session_row.user_id and actor.channel_user_id:
        return session_row.user_id == actor.channel_user_id
    if session_row.conversation_id and actor.member_id:
        return session_row.conversation_id == actor.member_id
    return False


def _can_delete_session(actor: ActorContext, session_row: ChatSessionRow) -> bool:
    if not _has_permission(actor, "assistant:sessions:delete:self"):
        return False
    if session_row.user_id and actor.channel_user_id:
        return session_row.user_id == actor.channel_user_id
    if session_row.conversation_id and actor.member_id:
        return session_row.conversation_id == actor.member_id
    return False


_FIRST_USER_TEXT_MAX = 80


def _truncate_first_user_text(text: str | None) -> str | None:
    if text is None:
        return None
    if len(text) <= _FIRST_USER_TEXT_MAX:
        return text
    return text[:_FIRST_USER_TEXT_MAX] + "…"


def _first_user_texts_by_session(
    session: Session,
    session_ids: list[str],
) -> dict[str, str | None]:
    """Return earliest role=user text_redacted per session (truncated)."""
    if not session_ids:
        return {}
    rows = session.execute(
        select(ChatMessageRow.session_id, ChatMessageRow.text_redacted)
        .where(
            ChatMessageRow.session_id.in_(session_ids),
            ChatMessageRow.role == "user",
        )
        .order_by(ChatMessageRow.created_at.asc())
    ).all()
    result: dict[str, str | None] = {sid: None for sid in session_ids}
    for session_id, text in rows:
        if result.get(session_id) is None:
            result[session_id] = _truncate_first_user_text(text or "")
    return result


def _session_json(
    row: ChatSessionRow,
    *,
    first_user_text: str | None = None,
    include_first_user_text: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "assistant_id": row.assistant_id,
        "team_id": row.team_id,
        "channel": row.channel,
        "conversation_type": row.conversation_type,
        "conversation_id": row.conversation_id,
        "user_id": row.user_id,
        "status": row.status,
        "prompt_release_id": row.prompt_release_id,
        "opened_at": row.opened_at.isoformat() if row.opened_at else None,
        "last_activity_at": row.last_activity_at.isoformat() if row.last_activity_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "close_reason": row.close_reason,
    }
    if include_first_user_text:
        payload["first_user_text"] = first_user_text
    return payload


def _message_json(row: ChatMessageRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "role": row.role,
        "text_redacted": row.text_redacted,
        "incoming_event_id": row.incoming_event_id,
        "meta_json": row.meta_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def register_session_routes(
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

    @app.get(
        "/api/assistant/v1/sessions",
        dependencies=[Depends(require_service_token)],
    )
    def list_sessions(
        team_id: str = Query(...),
        member_user_id: str | None = Query(None),
        status: str | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        stmt = select(ChatSessionRow).where(ChatSessionRow.team_id == team_id)
        if status:
            stmt = stmt.where(ChatSessionRow.status == status)
        if _has_permission(actor, "assistant:sessions:read:all"):
            if member_user_id:
                stmt = stmt.where(ChatSessionRow.user_id == member_user_id)
        else:
            effective_user = member_user_id or actor.channel_user_id
            if not effective_user:
                raise HTTPException(status_code=403, detail="缺少用户标识，无法限定 self 范围")
            stmt = stmt.where(ChatSessionRow.user_id == effective_user)
        count_stmt = select(func.count()).select_from(ChatSessionRow).where(
            ChatSessionRow.team_id == team_id
        )
        if status:
            count_stmt = count_stmt.where(ChatSessionRow.status == status)
        if _has_permission(actor, "assistant:sessions:read:all"):
            if member_user_id:
                count_stmt = count_stmt.where(ChatSessionRow.user_id == member_user_id)
        else:
            effective_user = member_user_id or actor.channel_user_id
            if not effective_user:
                raise HTTPException(status_code=403, detail="缺少用户标识，无法限定 self 范围")
            count_stmt = count_stmt.where(ChatSessionRow.user_id == effective_user)
        total = session.scalar(count_stmt) or 0
        rows = list(
            session.scalars(
                stmt.order_by(ChatSessionRow.last_activity_at.desc()).offset(offset).limit(limit)
            ).all()
        )
        first_texts = _first_user_texts_by_session(session, [row.id for row in rows])
        return {
            "items": [
                _session_json(
                    row,
                    first_user_text=first_texts.get(row.id),
                    include_first_user_text=True,
                )
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get(
        "/api/assistant/v1/sessions/{session_id}",
        dependencies=[Depends(require_service_token)],
    )
    def get_session(
        session_id: str,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        row = session.get(ChatSessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _can_access_session(actor, row):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        messages = session.scalars(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_at.asc())
        ).all()
        payload = _session_json(row)
        payload["messages"] = [_message_json(message) for message in messages]
        return payload

    @app.post(
        "/api/assistant/v1/sessions/{session_id}/close",
        dependencies=[Depends(require_service_token)],
    )
    def close_session_endpoint(
        session_id: str,
        body: CloseSessionBody,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        row = session.get(ChatSessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _can_access_session(actor, row):
            raise HTTPException(status_code=403, detail="无权关闭该会话")
        if row.status == "closed":
            return {"id": row.id, "status": row.status}
        close_session(session, row, reason=body.reason)
        session.commit()
        return {"id": row.id, "status": row.status}

    @app.get(
        "/api/assistant/v1/sessions/{session_id}/export",
        dependencies=[Depends(require_service_token)],
    )
    def export_session(
        session_id: str,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_export(actor)
        row = session.get(ChatSessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _can_access_session(actor, row):
            raise HTTPException(status_code=403, detail="无权导出该会话")
        messages = session.scalars(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_at.asc())
        ).all()
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "session": _session_json(row),
            "messages": [_message_json(message) for message in messages],
        }

    @app.delete(
        "/api/assistant/v1/sessions/{session_id}",
        dependencies=[Depends(require_service_token)],
    )
    def delete_session(
        session_id: str,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        row = session.get(ChatSessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _can_delete_session(actor, row) and not _has_permission(
            actor, "assistant:sessions:read:all"
        ):
            raise HTTPException(status_code=403, detail="无权删除该会话")
        if (
            not _can_delete_session(actor, row)
            and _has_permission(actor, "assistant:sessions:read:all")
            and actor.role != "owner"
        ):
            raise HTTPException(status_code=403, detail="仅 owner 可删除他人会话")

        messages = session.scalars(
            select(ChatMessageRow).where(ChatMessageRow.session_id == session_id)
        ).all()
        for message in messages:
            message.text_redacted = "[redacted]"
            message.secret_refs_json = []
            message.meta_json = {"redacted": True}
            session.add(message)

        purge_result = purge_session_memory(session, session_id, team_id=row.team_id)

        repo = AssistantRepository(session)
        repo.add_audit(
            assistant_id=row.assistant_id,
            team_id=row.team_id,
            action="session.deleted",
            detail=session_id,
            meta={
                "actor_member_id": actor.member_id,
                "message_count": len(messages),
                "archives_removed": purge_result.archives_removed,
                "atoms_removed": purge_result.atoms_removed,
            },
        )
        session.commit()
        return {
            "id": session_id,
            "status": "redacted",
            "messages_redacted": len(messages),
            "memory_purged": purge_result.archives_removed > 0 or purge_result.atoms_removed > 0,
        }
