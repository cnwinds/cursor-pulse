"""Idempotent close-session archive pipeline with staged retries."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from assistant_platform.config import AssistantConfig, resolve_effective_chat_memory
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_indexer import archive_session_messages, index_archived_session
from assistant_platform.memory.embedder import build_archive_embedder
from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.memory.contracts import ArchivePipelineStage, ArchivePipelineStatus, ArchiveStageStatus
from assistant_platform.memory.session_summary import generate_session_summary, load_session_summary
from assistant_platform.profiles.compiler import compile_and_persist_effective_profile
from assistant_platform.profiles.extractor import extract_profile_signals_from_session
from assistant_platform.memory.semantic.domain import (
    Commitment,
    Sensitivity,
    SemanticAtom,
    SourceVisibility,
    VisibilityContext,
    team_id_to_namespace,
)
from assistant_platform.memory.observability import log_archive_stage, safe_error_code
from assistant_platform.memory.opt_out import is_memory_opted_out
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository

logger = logging.getLogger(__name__)

_STAGE_ORDER = (
    ArchivePipelineStage.ARCHIVE,
    ArchivePipelineStage.INDEX,
    ArchivePipelineStage.SUMMARY,
    ArchivePipelineStage.FACTS,
    ArchivePipelineStage.PROFILE,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stage_key(stage: ArchivePipelineStage) -> str:
    return stage.value


def _read_stage_details(archive: SessionArchiveRow) -> dict[str, dict[str, Any]]:
    raw = archive.stage_details_json or {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def _write_stage_details(archive: SessionArchiveRow, details: dict[str, dict[str, Any]]) -> None:
    archive.stage_details_json = details


def get_stage_status(archive: SessionArchiveRow, stage: ArchivePipelineStage) -> ArchiveStageStatus:
    details = _read_stage_details(archive)
    payload = details.get(_stage_key(stage), {})
    status_value = payload.get("status")
    if not status_value:
        if stage == ArchivePipelineStage.ARCHIVE:
            status_value = archive.archive_status or ArchivePipelineStatus.PENDING.value
        elif stage == ArchivePipelineStage.INDEX:
            status_value = archive.index_status or ArchivePipelineStatus.PENDING.value
        else:
            status_value = ArchivePipelineStatus.PENDING.value
    return ArchiveStageStatus(
        stage=stage,
        status=ArchivePipelineStatus(status_value),
        attempt_count=int(payload.get("attempt_count") or 0),
        last_error=payload.get("last_error"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        details=dict(payload.get("details") or {}),
    )


def _set_stage_status(
    archive: SessionArchiveRow,
    stage: ArchivePipelineStage,
    *,
    status: ArchivePipelineStatus,
    attempt_count: int,
    last_error: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload = _read_stage_details(archive)
    entry = dict(payload.get(_stage_key(stage), {}))
    entry.update(
        {
            "status": status.value,
            "attempt_count": attempt_count,
            "last_error": last_error,
            "started_at": started_at.isoformat() if started_at else entry.get("started_at"),
            "finished_at": finished_at.isoformat() if finished_at else entry.get("finished_at"),
            "details": details or entry.get("details") or {},
        }
    )
    payload[_stage_key(stage)] = entry
    _write_stage_details(archive, payload)
    flag_modified(archive, "stage_details_json")

    if stage == ArchivePipelineStage.ARCHIVE:
        archive.archive_status = status.value
    elif stage == ArchivePipelineStage.INDEX:
        archive.index_status = status.value

    archive.updated_at = _utcnow()
    if last_error:
        archive.last_error = last_error[:500]
    elif status == ArchivePipelineStatus.READY:
        archive.last_error = None


def _aggregate_pipeline_status(archive: SessionArchiveRow) -> ArchivePipelineStatus:
    statuses = [get_stage_status(archive, stage).status for stage in _STAGE_ORDER]
    if any(status == ArchivePipelineStatus.FAILED for status in statuses):
        return ArchivePipelineStatus.FAILED
    if all(status == ArchivePipelineStatus.READY for status in statuses):
        return ArchivePipelineStatus.READY
    if any(status in (ArchivePipelineStatus.PARTIAL, ArchivePipelineStatus.READY) for status in statuses):
        return ArchivePipelineStatus.PARTIAL
    return ArchivePipelineStatus.PENDING


def _visibility_context(session_row: ChatSessionRow, user_id: str) -> VisibilityContext:
    if session_row.conversation_type == "group":
        return VisibilityContext.public()
    return VisibilityContext.private(user_id)


def _distill_facts_from_summary(
    session: Session,
    *,
    config: AssistantConfig,
    session_row: ChatSessionRow,
    archive: SessionArchiveRow,
) -> None:
    summary = load_session_summary(session, session_row.id)
    if summary is None:
        return

    from assistant_platform.llm import build_assistant_llm_client
    from assistant_platform.memory.semantic.distill import LlmDistiller, RuleBasedDistiller

    client = build_assistant_llm_client(config)
    distiller = LlmDistiller(client) if client else RuleBasedDistiller()
    repo = SemanticMemoryRepository(session)

    user_id = session_row.user_id or session_row.conversation_id
    if not user_id:
        return

    namespace = team_id_to_namespace(session_row.team_id)
    context = _visibility_context(session_row, user_id)
    source_vis = SourceVisibility.PUBLIC if context.is_public() else SourceVisibility.PRIVATE
    now = _utcnow()

    default_sens = Sensitivity.PUBLIC if context.is_public() else Sensitivity.CONFIDENTIAL

    existing = repo.list_atoms(namespace, [user_id])
    existing_for_session = {
        atom.content.strip().lower()
        for atom in existing
        if session_row.id in atom.evidence_session_ids
    }

    transcript_lines: list[str] = []
    for item in (*summary.facts, *summary.preferences):
        prefix = "事实:" if item.kind == "fact" else "偏好:"
        transcript_lines.append(f"{prefix} {item.content}")
    transcript = "\n".join(transcript_lines)
    if not transcript.strip():
        messages = session.scalars(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_row.id)
            .order_by(ChatMessageRow.created_at.asc())
        ).all()
        transcript = "\n".join(f"{m.role}: {m.text_redacted}" for m in messages)

    result = distiller.distill(
        namespace=namespace,
        subject_id=user_id,
        context=context,
        transcript=transcript,
    )

    for item in result.atoms:
        normalized = item.content.strip().lower()
        if normalized in existing_for_session:
            continue
        evidence_sessions = tuple(item.evidence_session_ids) or (session_row.id,)
        atom = SemanticAtom(
            id=str(uuid.uuid4()),
            namespace=namespace,
            subject_id=user_id,
            kind=item.kind,
            content=item.content,
            source_visibility=source_vis,
            sensitivity=default_sens,
            confidence=item.confidence,
            created_at=now,
            last_seen_at=now,
            first_confirmed_at=archive.archived_at or now,
            evidence_session_ids=evidence_sessions,
            evidence_chunk_ids=tuple(item.evidence_chunk_ids),
            evidence_message_seqs=tuple(item.evidence_message_seqs),
        )
        similar = repo.find_similar_atom(namespace, user_id, item.content)
        if similar:
            repo.touch_atom(similar.id, now)
        else:
            repo.upsert_atom(atom)

    for item in result.commitments:
        commitment = Commitment(
            id=str(uuid.uuid4()),
            namespace=namespace,
            counterparty_id=item.counterparty_id,
            type=item.type,
            statement=item.statement,
            scope=item.scope,
            status="active",
            created_at=now,
            first_confirmed_at=archive.archived_at or now,
            last_confirmed_at=now,
            evidence_session_ids=tuple(item.evidence_session_ids) or (session_row.id,),
        )
        repo.add_commitment(commitment)


def _run_stage(
    session: Session,
    *,
    config: AssistantConfig,
    session_row: ChatSessionRow,
    archive: SessionArchiveRow,
    stage: ArchivePipelineStage,
) -> None:
    chat_memory = resolve_effective_chat_memory(config)
    index_version = chat_memory.archive.index_version
    chunking = chat_memory.chunking
    embedding_enabled = chat_memory.embedding.enabled

    if stage == ArchivePipelineStage.ARCHIVE:
        archive_session_messages(session, session_row, index_version=index_version)
        return

    if stage == ArchivePipelineStage.INDEX:
        embedder, embedding_model = build_archive_embedder(
            embedding=chat_memory.embedding,
            llm_api_key=config.llm.api_key,
            llm_base_url=config.llm.base_url,
            llm_timeout_seconds=config.llm.timeout_seconds,
            llm_enabled=config.llm.enabled,
        )
        index_archived_session(
            session,
            session_row,
            index_version=index_version,
            max_tokens_per_chunk=chunking.max_tokens_per_chunk,
            overlap_tokens=chunking.overlap_tokens,
            embedding_enabled=embedding_enabled,
            embedder=embedder,
            embedding_model=embedding_model,
        )
        return

    if stage == ArchivePipelineStage.SUMMARY:
        generate_session_summary(session, session_row, archive)
        return

    if stage == ArchivePipelineStage.FACTS:
        if chat_memory.features.distill_on_close:
            _distill_facts_from_summary(session, config=config, session_row=session_row, archive=archive)
        return

    if stage == ArchivePipelineStage.PROFILE:
        if session_row.conversation_type == "group" or not session_row.user_id:
            return
        summary = load_session_summary(session, session_row.id)
        if summary is None:
            return
        extract_profile_signals_from_session(session, session_row, summary)
        compile_and_persist_effective_profile(
            session,
            user_id=session_row.user_id,
            team_id=session_row.team_id,
        )
        return

    raise ValueError(f"unknown pipeline stage: {stage}")


def _load_archive(session: Session, session_id: str) -> SessionArchiveRow | None:
    return session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)
    )


def run_archive_pipeline_stage(
    session: Session,
    *,
    config: AssistantConfig,
    session_row: ChatSessionRow,
    stage: ArchivePipelineStage,
) -> SessionArchiveRow:
    archive = _load_archive(session, session_row.id)
    current = get_stage_status(archive, stage) if archive else None
    if current and current.status == ArchivePipelineStatus.READY:
        return archive  # type: ignore[return-value]

    started = _utcnow()
    attempt = (current.attempt_count if current else 0) + 1
    if archive is None:
        if stage != ArchivePipelineStage.ARCHIVE:
            raise RuntimeError(f"archive header missing before stage {stage.value}")
        archive = archive_session_messages(
            session,
            session_row,
            index_version=resolve_effective_chat_memory(config).archive.index_version,
        )
        session.flush()
        archive = _load_archive(session, session_row.id)

    _set_stage_status(
        archive,
        stage,
        status=ArchivePipelineStatus.PARTIAL,
        attempt_count=attempt,
        started_at=started,
        last_error=None,
    )
    session.flush()

    try:
        _run_stage(session, config=config, session_row=session_row, archive=archive, stage=stage)
        archive = _load_archive(session, session_row.id)
        assert archive is not None
        finished = _utcnow()
        _set_stage_status(
            archive,
            stage,
            status=ArchivePipelineStatus.READY,
            attempt_count=attempt,
            finished_at=finished,
        )
        duration_ms = int((finished - started).total_seconds() * 1000)
        chunk_count = archive.chunk_total
        log_archive_stage(
            session_id=session_row.id,
            team_id=session_row.team_id,
            stage=stage.value,
            status=ArchivePipelineStatus.READY.value,
            duration_ms=duration_ms,
            attempt_count=attempt,
            chunk_count=chunk_count,
            index_version=archive.index_version,
        )
    except Exception as exc:
        archive = _load_archive(session, session_row.id)
        if archive is not None:
            _set_stage_status(
                archive,
                stage,
                status=ArchivePipelineStatus.FAILED,
                attempt_count=attempt,
                finished_at=_utcnow(),
                last_error=str(exc),
            )
            archive.status = ArchivePipelineStatus.FAILED.value
            session.flush()
        log_archive_stage(
            session_id=session_row.id,
            team_id=session_row.team_id,
            stage=stage.value,
            status=ArchivePipelineStatus.FAILED.value,
            attempt_count=attempt,
            error_code=safe_error_code(exc),
        )
        logger.exception("archive pipeline stage %s failed for %s", stage.value, session_row.id)
        raise

    archive.status = _aggregate_pipeline_status(archive).value
    session.flush()
    return archive


def _should_skip_archive_for_opt_out(session: Session, session_row: ChatSessionRow) -> bool:
    if session_row.conversation_type == "group":
        return False
    user_id = (session_row.user_id or session_row.conversation_id or "").strip()
    if not user_id:
        return False
    return is_memory_opted_out(session, user_id=user_id, team_id=session_row.team_id)


def run_archive_pipeline(
    session: Session,
    *,
    config: AssistantConfig,
    session_row: ChatSessionRow,
    stop_on_failure: bool = True,
) -> SessionArchiveRow:
    """Run all pending/failed stages in order; idempotent per stage."""
    if _should_skip_archive_for_opt_out(session, session_row):
        logger.info(
            "Skipping archive pipeline for session %s (user memory opt-out)",
            session_row.id,
        )
        existing = _load_archive(session, session_row.id)
        if existing is not None:
            return existing
        raise RuntimeError("archive skipped due to memory opt-out")

    archive: SessionArchiveRow | None = None
    for stage in _STAGE_ORDER:
        archive = _load_archive(session, session_row.id)
        status = get_stage_status(archive, stage).status if archive else ArchivePipelineStatus.PENDING
        if status == ArchivePipelineStatus.READY:
            continue
        try:
            archive = run_archive_pipeline_stage(
                session,
                config=config,
                session_row=session_row,
                stage=stage,
            )
        except Exception:
            if stop_on_failure:
                raise
    if archive is None:
        archive = run_archive_pipeline_stage(
            session,
            config=config,
            session_row=session_row,
            stage=ArchivePipelineStage.ARCHIVE,
        )
    return archive


def should_run_archive_pipeline(config: AssistantConfig) -> bool:
    chat_memory = resolve_effective_chat_memory(config)
    return bool(chat_memory.archive.enabled or chat_memory.features.archive_pipeline)
