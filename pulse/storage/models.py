from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("team_id", "dingtalk_user_id", name="uq_member_team_dingtalk"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(128))
    dingtalk_user_id: Mapped[str] = mapped_column(String(64), index=True)
    cursor_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    portal_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portal_permissions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_portal_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    submissions: Mapped[list[Submission]] = relationship(back_populates="member")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    billing_period: Mapped[str] = mapped_column(String(7), index=True)
    input_type: Mapped[str] = mapped_column(String(16))
    submit_channel: Mapped[str] = mapped_column(String(16))
    raw_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    member: Mapped[Member] = relationship(back_populates="submissions")
    usage_records: Mapped[list[UsageRecord]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_date: Mapped[Date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    max_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_input_cache_write: Mapped[int] = mapped_column(default=0)
    tokens_input_no_cache: Mapped[int] = mapped_column(default=0)
    tokens_cache_read: Mapped[int] = mapped_column(default=0)
    tokens_output: Mapped[int] = mapped_column(default=0)
    tokens_total: Mapped[int] = mapped_column(default=0)
    cost_raw: Mapped[str] = mapped_column(String(16))
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    cloud_agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    automation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row_hash: Mapped[str] = mapped_column(String(64), index=True)
    extraction_confidence: Mapped[float] = mapped_column(default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    submission: Mapped[Submission] = relationship(back_populates="usage_records")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    snapshot_type: Mapped[str] = mapped_column(String(16), default="monthly")
    metrics_json: Mapped[dict] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    computation_version: Mapped[str] = mapped_column(String(32))


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("metric_snapshots.id"), nullable=True)
    narrative: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    period: Mapped[str] = mapped_column(String(7))
    type: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    dingtalk_msg_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    query_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    alert_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TeamSetting(Base):
    __tablename__ = "team_settings"
    __table_args__ = (UniqueConstraint("team_id", "section", name="uq_team_setting_section"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    section: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_by_member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="web")
    action: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
