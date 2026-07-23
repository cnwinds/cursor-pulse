"""User memory controls: view, search, expand, export, opt-out, and cascade delete."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Callable

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.api.sessions import ActorContext, _actor_dependency, _has_permission
from assistant_platform.config import AssistantConfig, resolve_effective_chat_memory
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.memory.archive_models import ArchiveChunkRow, MemoryScope, SessionArchiveRow
from assistant_platform.memory.archive_search import (
    SearchScope,
    _session_is_in_scope,
    expand_neighbors,
    hybrid_search,
    resolve_search_scope,
)
from assistant_platform.memory.contracts import ChunkAnchor, MemoryScope as MemoryScopeEnum, RecallCursor
from assistant_platform.memory.deletion import (
    purge_all_personal_memory,
    purge_memory_atom,
    purge_session_memory,
)
from assistant_platform.memory.opt_out import (
    clear_memory_opt_out,
    get_memory_opt_out,
    set_memory_opt_out,
)
from assistant_platform.memory.session_summary import SessionSummaryRow, load_session_summary
from assistant_platform.profiles.compiler import compile_profile_guidance
from assistant_platform.profiles.models import ProfileEffectiveRow
from assistant_platform.storage.repository import AssistantRepository
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository
from assistant_platform.memory.semantic.domain import team_id_to_namespace


class MemoryExpandBody(BaseModel):
    session_id: str
    chunk_index: int
    start_seq: int
    end_seq: int
    team_id: str
    user_id: str | None = None
    conversation_type: str = "private"
    conversation_id: str | None = None


class MemoryOptOutBody(BaseModel):
    user_id: str
    team_id: str


def _require_memory_read(actor: ActorContext) -> None:
    if _has_permission(actor, "assistant:sessions:read:all"):
        return
    if _has_permission(actor, "assistant:sessions:read:self"):
        return
    raise HTTPException(status_code=403, detail="缺少 assistant:sessions:read 权限")


def _require_memory_export(actor: ActorContext) -> None:
    if _has_permission(actor, "assistant:sessions:export:all"):
        return
    if _has_permission(actor, "assistant:sessions:export:self"):
        return
    raise HTTPException(status_code=403, detail="缺少 assistant:sessions:export 权限")


def _require_memory_delete(actor: ActorContext) -> None:
    if _has_permission(actor, "assistant:sessions:delete:self"):
        return
    raise HTTPException(status_code=403, detail="缺少 assistant:sessions:delete 权限")


def _ensure_self_scope(actor: ActorContext, user_id: str, *, allow_all: bool = False) -> None:
    if allow_all and _has_permission(actor, "assistant:sessions:read:all"):
        return
    if actor.channel_user_id and actor.channel_user_id != user_id:
        raise HTTPException(status_code=403, detail="只能访问自己的记忆")


def _resolve_subject(
    *,
    user_id: str,
    conversation_type: str,
    conversation_id: str | None,
) -> str:
    if conversation_type == "group":
        return (conversation_id or user_id).strip()
    return user_id.strip()


def _build_search_scope(
    *,
    team_id: str,
    user_id: str,
    conversation_type: str = "private",
    conversation_id: str | None = None,
) -> SearchScope:
    subject = _resolve_subject(
        user_id=user_id,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
    )
    return resolve_search_scope(
        team_id=team_id,
        subject_id=subject,
        conversation_type=conversation_type,
        conversation_id=conversation_id or subject,
        user_id=user_id if conversation_type != "group" else None,
    )


def _archive_summary_json(row: SessionArchiveRow) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "team_id": row.team_id,
        "scope": row.scope,
        "subject_id": row.subject_id,
        "status": row.status,
        "archive_status": row.archive_status,
        "index_status": row.index_status,
        "message_total": row.message_total,
        "chunk_total": row.chunk_total,
        "occurred_from": row.occurred_from.isoformat() if row.occurred_from else None,
        "occurred_to": row.occurred_to.isoformat() if row.occurred_to else None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


def register_memory_routes(
    app,
    *,
    config: AssistantConfig,
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
        "/api/assistant/v1/memories",
        dependencies=[Depends(require_service_token)],
    )
    def list_memories(
        team_id: str = Query(...),
        user_id: str = Query(...),
        conversation_type: str = Query("private"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_read(actor)
        _ensure_self_scope(actor, user_id, allow_all=True)
        scope_value = MemoryScope.PERSONAL.value if conversation_type != "group" else MemoryScope.GROUP.value
        subject_id = _resolve_subject(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=user_id if conversation_type == "group" else None,
        )
        stmt = (
            select(SessionArchiveRow)
            .where(
                SessionArchiveRow.team_id == team_id,
                SessionArchiveRow.subject_id == subject_id,
                SessionArchiveRow.scope == scope_value,
                SessionArchiveRow.index_status == "ready",
            )
            .order_by(SessionArchiveRow.archived_at.desc())
        )
        rows = session.scalars(stmt.offset(offset).limit(limit)).all()
        namespace = team_id_to_namespace(team_id)
        repo = SemanticMemoryRepository(session)
        atoms = repo.list_atoms(namespace, [subject_id]) if conversation_type != "group" else []
        effective = session.scalar(
            select(ProfileEffectiveRow).where(
                ProfileEffectiveRow.user_id == user_id,
                ProfileEffectiveRow.team_id == team_id,
            )
        )
        return {
            "team_id": team_id,
            "user_id": user_id,
            "archives": [_archive_summary_json(row) for row in rows],
            "facts": [
                {
                    "id": atom.id,
                    "kind": atom.kind.value,
                    "content": atom.content,
                    "confidence": atom.confidence,
                    "evidence_session_ids": list(atom.evidence_session_ids),
                }
                for atom in atoms
            ],
            "effective_profile": effective.snapshot_json if effective else None,
            "limit": limit,
            "offset": offset,
        }

    @app.get(
        "/api/assistant/v1/memories/search",
        dependencies=[Depends(require_service_token)],
    )
    def search_memories(
        team_id: str = Query(...),
        user_id: str = Query(...),
        query: str = Query(..., min_length=1),
        conversation_type: str = Query("private"),
        conversation_id: str | None = Query(None),
        cursor: str | None = Query(None),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_read(actor)
        _ensure_self_scope(actor, user_id, allow_all=True)
        scope = _build_search_scope(
            team_id=team_id,
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
        )
        chat_memory = resolve_effective_chat_memory(config)
        parsed_cursor: RecallCursor | None = None
        if cursor:
            try:
                import json

                parsed_cursor = RecallCursor.model_validate(json.loads(cursor))
            except Exception as exc:
                raise HTTPException(status_code=400, detail="invalid cursor") from exc
        hits, page = hybrid_search(
            session,
            query=query,
            scope=scope,
            config=chat_memory,
            cursor=parsed_cursor,
        )
        return {
            "fragments": [hit.model_dump(mode="json") for hit in hits],
            "page": page.model_dump(mode="json"),
        }

    @app.post(
        "/api/assistant/v1/memories/expand",
        dependencies=[Depends(require_service_token)],
    )
    def expand_memory(
        body: MemoryExpandBody,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_read(actor)
        effective_user = body.user_id or body.conversation_id or ""
        _ensure_self_scope(actor, effective_user, allow_all=True)
        scope = _build_search_scope(
            team_id=body.team_id,
            user_id=effective_user,
            conversation_type=body.conversation_type,
            conversation_id=body.conversation_id,
        )
        anchor = ChunkAnchor(
            session_id=body.session_id,
            chunk_index=body.chunk_index,
            start_seq=body.start_seq,
            end_seq=body.end_seq,
        )
        chat_memory = resolve_effective_chat_memory(config)
        if not _session_is_in_scope(session, body.session_id, scope):
            raise HTTPException(status_code=404, detail="片段不可用或超出作用域")
        window = expand_neighbors(
            session,
            anchor=anchor,
            scope=scope,
            neighbor_count=chat_memory.recall.expand_neighbor_count,
        )
        return window.model_dump(mode="json")

    @app.get(
        "/api/assistant/v1/memories/sessions/{session_id}/summary",
        dependencies=[Depends(require_service_token)],
    )
    def get_memory_session_summary(
        session_id: str,
        team_id: str = Query(...),
        user_id: str = Query(...),
        conversation_type: str = Query("private"),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_read(actor)
        _ensure_self_scope(actor, user_id, allow_all=True)
        scope = _build_search_scope(
            team_id=team_id,
            user_id=user_id,
            conversation_type=conversation_type,
        )
        archive = session.scalar(
            select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)
        )
        if archive is None:
            raise HTTPException(status_code=404, detail="归档不存在")
        if archive.team_id != scope.team_id or archive.subject_id != scope.subject_id:
            raise HTTPException(status_code=403, detail="无权访问该会话摘要")
        if archive.scope != scope.scope.value:
            raise HTTPException(status_code=403, detail="作用域不匹配")
        summary = load_session_summary(session, session_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="摘要不存在")
        return summary.model_dump(mode="json")

    @app.get(
        "/api/assistant/v1/memories/export",
        dependencies=[Depends(require_service_token)],
    )
    def export_memories(
        team_id: str = Query(...),
        user_id: str = Query(...),
        conversation_type: str = Query("private"),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_export(actor)
        _ensure_self_scope(actor, user_id, allow_all=True)
        scope_value = MemoryScope.PERSONAL.value if conversation_type != "group" else MemoryScope.GROUP.value
        subject_id = _resolve_subject(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=user_id if conversation_type == "group" else None,
        )
        archives = session.scalars(
            select(SessionArchiveRow).where(
                SessionArchiveRow.team_id == team_id,
                SessionArchiveRow.subject_id == subject_id,
                SessionArchiveRow.scope == scope_value,
            )
        ).all()
        summaries = session.scalars(
            select(SessionSummaryRow).where(
                SessionSummaryRow.team_id == team_id,
                SessionSummaryRow.subject_id == subject_id,
                SessionSummaryRow.scope == scope_value,
            )
        ).all()
        namespace = team_id_to_namespace(team_id)
        repo = SemanticMemoryRepository(session)
        atoms = repo.list_atoms(namespace, [subject_id]) if conversation_type != "group" else []
        guidance = (
            compile_profile_guidance(session, user_id=user_id, team_id=team_id)
            if conversation_type != "group"
            else None
        )
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "team_id": team_id,
            "user_id": user_id,
            "archives": [_archive_summary_json(row) for row in archives],
            "summaries": [row.summary_json for row in summaries],
            "facts": [
                {
                    "id": atom.id,
                    "kind": atom.kind.value,
                    "content": atom.content,
                    "confidence": atom.confidence,
                    "evidence_session_ids": list(atom.evidence_session_ids),
                }
                for atom in atoms
            ],
            "profile_guidance": guidance.model_dump(mode="json") if guidance else None,
        }

    @app.get(
        "/api/assistant/v1/memories/opt-out",
        dependencies=[Depends(require_service_token)],
    )
    def get_opt_out_status(
        team_id: str = Query(...),
        user_id: str = Query(...),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_read(actor)
        _ensure_self_scope(actor, user_id)
        row = get_memory_opt_out(session, user_id=user_id, team_id=team_id)
        return {
            "user_id": user_id,
            "team_id": team_id,
            "opted_out": row is not None,
            "opted_out_at": row.opted_out_at.isoformat() if row else None,
        }

    @app.post(
        "/api/assistant/v1/memories/opt-out",
        dependencies=[Depends(require_service_token)],
    )
    def set_opt_out(
        body: MemoryOptOutBody,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_delete(actor)
        _ensure_self_scope(actor, body.user_id)
        row = set_memory_opt_out(session, user_id=body.user_id, team_id=body.team_id)
        repo = AssistantRepository(session)
        repo.add_audit(
            assistant_id=config.assistant_id,
            team_id=body.team_id,
            action="memory.opt_out",
            detail=body.user_id,
            meta={"actor_member_id": actor.member_id},
        )
        session.commit()
        return {
            "user_id": body.user_id,
            "team_id": body.team_id,
            "opted_out": True,
            "opted_out_at": row.opted_out_at.isoformat(),
        }

    @app.delete(
        "/api/assistant/v1/memories/opt-out",
        dependencies=[Depends(require_service_token)],
    )
    def clear_opt_out(
        team_id: str = Query(...),
        user_id: str = Query(...),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_delete(actor)
        _ensure_self_scope(actor, user_id)
        cleared = clear_memory_opt_out(session, user_id=user_id, team_id=team_id)
        session.commit()
        return {"user_id": user_id, "team_id": team_id, "opted_out": not cleared}

    @app.delete(
        "/api/assistant/v1/memories/items/{item_id}",
        dependencies=[Depends(require_service_token)],
    )
    def delete_memory_item(
        item_id: str,
        team_id: str = Query(...),
        user_id: str = Query(...),
        source_type: str = Query("atom"),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_delete(actor)
        _ensure_self_scope(actor, user_id)
        namespace = team_id_to_namespace(team_id)
        deleted = False
        if source_type == "atom":
            deleted = purge_memory_atom(
                session,
                item_id,
                namespace=namespace,
                subject_id=user_id,
            )
        elif source_type == "chunk":
            chunk = session.get(ArchiveChunkRow, item_id)
            if (
                chunk is not None
                and chunk.team_id == team_id
                and chunk.subject_id == user_id
                and chunk.scope == MemoryScopeEnum.PERSONAL.value
            ):
                purge_session_memory(session, chunk.session_id, team_id=team_id)
                deleted = True
        if not deleted:
            raise HTTPException(status_code=404, detail="记忆项不存在或无权删除")
        repo = AssistantRepository(session)
        repo.add_audit(
            assistant_id=config.assistant_id,
            team_id=team_id,
            action="memory.item.deleted",
            detail=item_id,
            meta={
                "actor_member_id": actor.member_id,
                "source_type": source_type,
                "user_id": user_id,
            },
        )
        session.commit()
        return {"deleted": True, "id": item_id, "source_type": source_type}

    @app.delete(
        "/api/assistant/v1/memories/sessions/{session_id}",
        dependencies=[Depends(require_service_token)],
    )
    def delete_session_memory(
        session_id: str,
        team_id: str = Query(...),
        user_id: str = Query(...),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_delete(actor)
        _ensure_self_scope(actor, user_id)
        archive = session.scalar(
            select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)
        )
        if archive is None:
            raise HTTPException(status_code=404, detail="会话归档不存在")
        if archive.team_id != team_id:
            raise HTTPException(status_code=403, detail="团队不匹配")
        if archive.scope == MemoryScope.PERSONAL.value and archive.subject_id != user_id:
            raise HTTPException(status_code=403, detail="无权删除该会话记忆")
        result = purge_session_memory(session, session_id, team_id=team_id)
        repo = AssistantRepository(session)
        repo.add_audit(
            assistant_id=config.assistant_id,
            team_id=team_id,
            action="memory.session.deleted",
            detail=session_id,
            meta={
                "actor_member_id": actor.member_id,
                "user_id": user_id,
                "archives_removed": result.archives_removed,
                "atoms_removed": result.atoms_removed,
            },
        )
        session.commit()
        return {
            "deleted": True,
            "session_id": session_id,
            "archives_removed": result.archives_removed,
            "summaries_removed": result.summaries_removed,
            "atoms_removed": result.atoms_removed,
            "commitments_removed": result.commitments_removed,
        }

    @app.delete(
        "/api/assistant/v1/memories/all",
        dependencies=[Depends(require_service_token)],
    )
    def delete_all_memories(
        team_id: str = Query(...),
        user_id: str = Query(...),
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_memory_delete(actor)
        _ensure_self_scope(actor, user_id)
        result = purge_all_personal_memory(session, user_id=user_id, team_id=team_id)
        repo = AssistantRepository(session)
        repo.add_audit(
            assistant_id=config.assistant_id,
            team_id=team_id,
            action="memory.all.deleted",
            detail=user_id,
            meta={
                "actor_member_id": actor.member_id,
                "sessions_processed": result.sessions_processed,
                "atoms_removed": result.atoms_removed,
            },
        )
        session.commit()
        return {
            "deleted": True,
            "user_id": user_id,
            "team_id": team_id,
            "sessions_processed": result.sessions_processed,
            "atoms_removed": result.atoms_removed,
            "profile_signals_removed": result.profile_signals_removed,
        }
