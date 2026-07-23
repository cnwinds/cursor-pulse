"""Structured close-session summaries with evidence-backed items."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.memory.archive_models import ArchiveChunkRow, ArchiveMessageRow, resolve_archive_scope
from assistant_platform.memory.contracts import (
    ArchivePipelineStatus,
    MemoryScope,
    SessionSummary,
    SessionSummaryEvidence,
    SessionSummaryItem,
)
from assistant_platform.storage.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class SessionSummaryRow(Base):
    __tablename__ = "ap_session_summaries"
    __table_args__ = (UniqueConstraint("session_id", name="uq_ap_session_summaries_session"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    pipeline_status: Mapped[str] = mapped_column(String(16), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def _first_user_goal(messages: list[ArchiveMessageRow]) -> str:
    for message in messages:
        if message.role == "user" and (message.text_redacted or "").strip():
            return (message.text_redacted or "").strip()[:240]
    return ""


def _infer_outcome(messages: list[ArchiveMessageRow]) -> str:
    for message in reversed(messages):
        if message.role == "assistant" and (message.text_redacted or "").strip():
            meta = message.meta_json or {}
            if str(meta.get("kind") or "").lower() == "interim":
                continue
            return (message.text_redacted or "").strip()[:240]
    return ""


def _evidence_for_message(
    *,
    session_id: str,
    message: ArchiveMessageRow,
    chunks: list[ArchiveChunkRow],
) -> SessionSummaryEvidence:
    chunk_id = None
    for chunk in chunks:
        if chunk.start_seq <= message.seq <= chunk.end_seq:
            chunk_id = chunk.id
            break
    return SessionSummaryEvidence(
        evidence_id=f"{session_id}:{message.seq}",
        chunk_id=chunk_id,
        message_seq=message.seq,
        occurred_at=message.created_at,
        confidence=0.8,
    )


def _parse_prefixed_items(
    messages: list[ArchiveMessageRow],
    *,
    session_id: str,
    chunks: list[ArchiveChunkRow],
    prefix: str,
    kind: str,
) -> list[SessionSummaryItem]:
    items: list[SessionSummaryItem] = []
    pattern = re.compile(rf"^{re.escape(prefix)}\s*(.+)$", re.IGNORECASE)
    for message in messages:
        if message.role != "user":
            continue
        text = (message.text_redacted or "").strip()
        match = pattern.match(text)
        if not match:
            continue
        content = match.group(1).strip()
        if not content:
            continue
        items.append(
            SessionSummaryItem(
                content=content,
                kind=kind,
                confidence=0.85 if kind == "preference" else 0.8,
                evidence=(_evidence_for_message(session_id=session_id, message=message, chunks=chunks),),
            )
        )
    return items


def _detect_commitments(
    messages: list[ArchiveMessageRow],
    *,
    session_id: str,
    chunks: list[ArchiveChunkRow],
) -> list[SessionSummaryItem]:
    items: list[SessionSummaryItem] = []
    commitment_re = re.compile(r"(?:我(?:答应|承诺)|会帮你|下周.*?(?:给|发|完成))", re.IGNORECASE)
    for message in messages:
        text = (message.text_redacted or "").strip()
        if not text or not commitment_re.search(text):
            continue
        items.append(
            SessionSummaryItem(
                content=text[:240],
                kind="commitment",
                confidence=0.75,
                evidence=(_evidence_for_message(session_id=session_id, message=message, chunks=chunks),),
            )
        )
    return items


def build_session_summary_from_archive(
    session_row: ChatSessionRow,
    messages: list[ArchiveMessageRow],
    chunks: list[ArchiveChunkRow],
    *,
    archived_at: datetime | None = None,
) -> SessionSummary:
    scope, subject_id = resolve_archive_scope(
        conversation_type=session_row.conversation_type,
        user_id=session_row.user_id,
        conversation_id=session_row.conversation_id,
    )
    ordered = sorted(messages, key=lambda m: m.seq)
    facts = _parse_prefixed_items(
        ordered, session_id=session_row.id, chunks=chunks, prefix="事实:", kind="fact"
    )
    preferences = _parse_prefixed_items(
        ordered, session_id=session_row.id, chunks=chunks, prefix="偏好:", kind="preference"
    )
    commitments = _detect_commitments(ordered, session_id=session_row.id, chunks=chunks)
    user_goal = _first_user_goal(ordered)
    outcome = _infer_outcome(ordered)
    topic = user_goal[:80] if user_goal else ""
    narrative_parts = []
    if user_goal:
        narrative_parts.append(f"用户目标: {user_goal}")
    if outcome:
        narrative_parts.append(f"结果: {outcome}")
    if facts:
        narrative_parts.append("事实: " + "; ".join(item.content for item in facts[:3]))
    return SessionSummary(
        session_id=session_row.id,
        scope=scope,
        subject_id=subject_id,
        team_id=session_row.team_id,
        topic=topic,
        user_goal=user_goal,
        outcome=outcome,
        facts=tuple(facts),
        commitments=tuple(commitments),
        open_items=(),
        preferences=tuple(preferences),
        narrative_summary=" ".join(narrative_parts),
        archived_at=archived_at,
        pipeline_status=ArchivePipelineStatus.READY,
    )


def summary_content_hash(summary: SessionSummary) -> str:
    parts = [
        summary.topic,
        summary.user_goal,
        summary.outcome,
        summary.narrative_summary,
        "|".join(item.content for item in summary.facts),
        "|".join(item.content for item in summary.commitments),
        "|".join(item.content for item in summary.preferences),
    ]
    return _content_hash("\n".join(parts))


def summary_to_json(summary: SessionSummary) -> dict:
    return summary.model_dump(mode="json")


def summary_from_json(data: dict) -> SessionSummary:
    return SessionSummary.model_validate(data)


def load_session_summary(session: Session, session_id: str) -> SessionSummary | None:
    row = session.scalar(select(SessionSummaryRow).where(SessionSummaryRow.session_id == session_id))
    if row is None or not row.summary_json:
        return None
    return summary_from_json(row.summary_json)


def upsert_session_summary(
    session: Session,
    summary: SessionSummary,
    *,
    content_hash: str | None = None,
) -> SessionSummaryRow:
    digest = content_hash or summary_content_hash(summary)
    row = session.scalar(
        select(SessionSummaryRow).where(SessionSummaryRow.session_id == summary.session_id)
    )
    now = _utcnow()
    payload = summary_to_json(summary)
    if row is None:
        row = SessionSummaryRow(
            session_id=summary.session_id,
            team_id=summary.team_id,
            scope=summary.scope.value,
            subject_id=summary.subject_id,
            content_hash=digest,
            summary_json=payload,
            pipeline_status=ArchivePipelineStatus.READY.value,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    elif row.content_hash == digest:
        return row
    else:
        row.team_id = summary.team_id
        row.scope = summary.scope.value
        row.subject_id = summary.subject_id
        row.content_hash = digest
        row.summary_json = payload
        row.pipeline_status = ArchivePipelineStatus.READY.value
        row.updated_at = now
    session.flush()
    return row


def generate_session_summary(
    session: Session,
    session_row: ChatSessionRow,
    archive_header,
) -> SessionSummary:
    messages = list(
        session.scalars(
            select(ArchiveMessageRow)
            .where(ArchiveMessageRow.session_id == session_row.id)
            .order_by(ArchiveMessageRow.seq.asc())
        ).all()
    )
    chunks = list(
        session.scalars(
            select(ArchiveChunkRow)
            .where(ArchiveChunkRow.session_id == session_row.id)
            .order_by(ArchiveChunkRow.chunk_index.asc())
        ).all()
    )
    summary = build_session_summary_from_archive(
        session_row,
        messages,
        chunks,
        archived_at=getattr(archive_header, "archived_at", None),
    )
    upsert_session_summary(session, summary)
    return summary
