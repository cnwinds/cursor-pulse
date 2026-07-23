from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AssistantRow(Base):
    __tablename__ = "ap_assistants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="小脉")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class IncomingEventRow(Base):
    __tablename__ = "ap_incoming_events"
    __table_args__ = (
        UniqueConstraint("channel", "channel_message_id", name="uq_ap_channel_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    channel_message_id: Mapped[str] = mapped_column(String(128))
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    sender_channel_user_id: Mapped[str] = mapped_column(String(128), index=True)
    sender_display_name: Mapped[str] = mapped_column(String(128), default="")
    conversation_type: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    reply_endpoint_json: Mapped[dict] = mapped_column(JSON, default=dict)
    text_redacted: Mapped[str] = mapped_column(Text, default="")
    secret_refs_json: Mapped[list] = mapped_column(JSON, default=list)
    attachments_json: Mapped[list] = mapped_column(JSON, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    raw_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OutboxEventRow(Base):
    __tablename__ = "ap_outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackgroundJobRow(Base):
    __tablename__ = "ap_background_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SkillEmbeddingRow(Base):
    """Vector routing card for a single skill markdown file.

    One row per skill file (skill_id = docs-relative POSIX path without ``.md``).
    Lifecycle is independent from archive/chat-memory embeddings; rows are synced
    by file ``content_hash`` and used to route which skill cards are injected each
    turn.
    """

    __tablename__ = "ap_skill_embeddings"

    skill_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    rel_path: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    audience_json: Mapped[list] = mapped_column(JSON, default=list)
    embedding_json: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditEventRow(Base):
    __tablename__ = "ap_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
