"""SQLAlchemy tables for semantic memory (facts/commitments/disclosure logs).

Replaces the legacy ``personamem`` ``pm_*`` tables with ``ap_*`` equivalents on
the shared assistant_platform ``Base``, so they are created/migrated alongside
every other assistant table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from assistant_platform.storage.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SemanticAtomRow(Base):
    __tablename__ = "ap_semantic_atoms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    namespace: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    source_visibility: Mapped[str] = mapped_column(String(16))
    sensitivity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    first_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)


class CommitmentRow(Base):
    __tablename__ = "ap_commitments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    namespace: Mapped[str] = mapped_column(String(128), index=True)
    counterparty_id: Mapped[str] = mapped_column(String(128), index=True)
    type: Mapped[str] = mapped_column(String(16))
    statement: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")
    first_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DisclosureLogRow(Base):
    __tablename__ = "ap_disclosure_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    namespace: Mapped[str] = mapped_column(String(128), index=True)
    visibility: Mapped[str] = mapped_column(String(16))
    audience_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    query_excerpt: Mapped[str] = mapped_column(Text)
    released_atom_ids: Mapped[list] = mapped_column(JSON, default=list)
    blocked_atom_ids: Mapped[list] = mapped_column(JSON, default=list)
    deflection_reason: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
