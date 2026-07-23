from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_platform.storage.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityDefinitionRow(Base):
    __tablename__ = "ap_capability_definitions"
    __table_args__ = (UniqueConstraint("key", name="uq_ap_cap_def_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CapabilityVersionRow(Base):
    __tablename__ = "ap_capability_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_ap_cap_ver_def_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    definition_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[str] = mapped_column(String(16))
    risk_level: Mapped[str] = mapped_column(String(16))
    input_schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_type: Mapped[str] = mapped_column(String(32), default="pulse_http")
    provider_operation: Mapped[str] = mapped_column(String(128))
    prompt_instruction: Mapped[str] = mapped_column(Text, default="")
    idempotency_required: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CapabilityPackRow(Base):
    __tablename__ = "ap_capability_packs"
    __table_args__ = (
        UniqueConstraint("team_id", "key", name="uq_ap_cap_pack_team_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    key: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CapabilityPackItemRow(Base):
    __tablename__ = "ap_capability_pack_items"
    __table_args__ = (
        UniqueConstraint("pack_id", "capability_key", name="uq_ap_cap_pack_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    pack_id: Mapped[str] = mapped_column(String(36), index=True)
    capability_key: Mapped[str] = mapped_column(String(128))
    capability_version: Mapped[str] = mapped_column(String(16), default="1")


class CapabilityAssignmentRow(Base):
    __tablename__ = "ap_capability_assignments"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "scope_type",
            "scope_id",
            "pack_id",
            name="uq_ap_cap_assign_pack",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(String(64), default="")
    pack_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    capability_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ToolInvocationRow(Base):
    __tablename__ = "ap_tool_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    invocation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    assistant_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    capability_key: Mapped[str] = mapped_column(String(128), index=True)
    capability_version: Mapped[str] = mapped_column(String(16), default="1")
    actor_member_id: Mapped[str] = mapped_column(String(36), index=True, default="")
    actor_channel_user_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="planned")
    request_redacted_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_redacted_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
