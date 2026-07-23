"""Permanent session archive ORM models (Phase 2).

Ledger messages in ``ap_chat_*`` remain the operational store. Closed-session
snapshots live here permanently until explicit user/admin deletion. Retrieval
chunks and FTS/vector indexes are derived from these rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_platform.memory.contracts import MemoryScope
from assistant_platform.storage.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_archive_scope(
    *,
    conversation_type: str,
    user_id: str | None,
    conversation_id: str,
) -> tuple[MemoryScope, str]:
    """Map chat session identity to memory scope + subject_id."""
    if conversation_type == "group":
        return MemoryScope.GROUP, conversation_id
    subject = (user_id or conversation_id or "").strip()
    return MemoryScope.PERSONAL, subject


class SessionArchiveRow(Base):
    """Per-session permanent archive header and pipeline stage status."""

    __tablename__ = "ap_session_archives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="")
    conversation_type: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    archive_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    index_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    index_version: Mapped[int] = mapped_column(Integer, default=2)
    message_total: Mapped[int] = mapped_column(Integer, default=0)
    chunk_total: Mapped[int] = mapped_column(Integer, default=0)
    occurred_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ArchiveMessageRow(Base):
    """Permanent redacted message snapshot with stable session sequence."""

    __tablename__ = "ap_archive_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_ap_archive_messages_session_seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    source_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))
    text_redacted: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ArchiveChunkRow(Base):
    """Searchable archive fragment (user + final assistant turns)."""

    __tablename__ = "ap_archive_chunks"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "chunk_index",
            "index_version",
            name="uq_ap_archive_chunks_session_idx_ver",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_seq: Mapped[int] = mapped_column(Integer)
    end_seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_roles_json: Mapped[list] = mapped_column(JSON, default=list)
    source_message_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    occurred_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurred_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    index_version: Mapped[int] = mapped_column(Integer, default=2, index=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
