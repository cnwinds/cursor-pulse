from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
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
    portal_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portal_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portal_permissions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_portal_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager_dingtalk_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manager_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True, index=True
    )
    employment_status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    ingestions: Mapped[list[UsageIngestion]] = relationship(
        back_populates="member",
        foreign_keys="UsageIngestion.member_id",
    )


class UsageIngestion(Base):
    __tablename__ = "usage_ingestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), index=True, nullable=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("ai_accounts.id"), index=True, nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("ai_vendors.id"), index=True, nullable=True)
    billing_period: Mapped[str] = mapped_column(String(7), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    triggered_by: Mapped[str] = mapped_column(String(36), default="system")
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_snapshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    member: Mapped[Member | None] = relationship(back_populates="ingestions", foreign_keys=[member_id])
    usage_records: Mapped[list[UsageRecord]] = relationship(
        back_populates="ingestion", cascade="all, delete-orphan"
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ingestion_id: Mapped[str] = mapped_column(ForeignKey("usage_ingestions.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
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
    cost_estimated_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    cost_basis: Mapped[str] = mapped_column(String(16), default="none")
    pricing_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pricing_rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cloud_agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    automation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row_hash: Mapped[str] = mapped_column(String(64), index=True)
    extraction_confidence: Mapped[float] = mapped_column(default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    ingestion: Mapped[UsageIngestion] = relationship(back_populates="usage_records")


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


class AiVendor(Base):
    __tablename__ = "ai_vendors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    plans: Mapped[list[AiPlan]] = relationship(back_populates="vendor")


class AiPlan(Base):
    __tablename__ = "ai_plans"
    __table_args__ = (
        UniqueConstraint("vendor_id", "slug", "effective_from", name="uq_plan_vendor_slug_from"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("ai_vendors.id"), index=True)
    plan_name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64))
    billing_type: Mapped[str] = mapped_column(String(32))
    price_amount: Mapped[float] = mapped_column(Numeric(12, 4))
    price_currency: Mapped[str] = mapped_column(String(8))
    billing_cycle: Mapped[str] = mapped_column(String(16), default="monthly")
    included_quota: Mapped[dict] = mapped_column(JSON, default=dict)
    quota_ratio_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quota_denominator: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    upgrade_threshold_pct: Mapped[float] = mapped_column(Float, default=95.0)
    upgrade_consecutive_months: Mapped[int] = mapped_column(Integer, default=2)
    usage_submit_methods: Mapped[list] = mapped_column(JSON, default=list)
    official_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    superseded_by_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_plans.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    vendor: Mapped[AiVendor] = relationship(back_populates="plans")
    accounts: Mapped[list[AiAccount]] = relationship(back_populates="plan")


class AiAccount(Base):
    __tablename__ = "ai_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("ai_vendors.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("ai_plans.id"), index=True)
    account_identifier: Mapped[str] = mapped_column(String(256), index=True)
    ownership: Mapped[str] = mapped_column(String(16), default="company")
    status: Mapped[str] = mapped_column(String(16), default="shared")
    primary_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True, index=True
    )
    shared_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    monthly_budget_cap: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    budget_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    renews_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    usage_resets_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    resets_on_source: Mapped[str] = mapped_column(String(16), default="manual")
    suggest_dedicated: Mapped[bool] = mapped_column(Boolean, default=False)
    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    plan: Mapped[AiPlan] = relationship(back_populates="accounts")
    vendor: Mapped[AiVendor] = relationship()
    secondary_members: Mapped[list[AiAccountMember]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    usage_summaries: Mapped[list[UsageSummary]] = relationship(back_populates="account")
    plan_history: Mapped[list[AiAccountPlanHistory]] = relationship(
        back_populates="account",
        order_by="AiAccountPlanHistory.effective_from",
    )


class AiAccountCredential(Base):
    __tablename__ = "ai_account_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("ai_vendors.id"), index=True)
    credential_type: Mapped[str] = mapped_column(String(32))
    encrypted_value: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))
    key_role: Mapped[str] = mapped_column(String(16), default="primary")
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignee_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    bound_by_member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(16), default="never")
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_priority: Mapped[str] = mapped_column(String(16), default="normal")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    sync_jitter_sec: Mapped[int] = mapped_column(Integer, default=0)
    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class AiAccountMember(Base):
    __tablename__ = "ai_account_members"

    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="secondary")

    account: Mapped[AiAccount] = relationship(back_populates="secondary_members")


class AiAccountPlanHistory(Base):
    __tablename__ = "ai_account_plan_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("ai_plans.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    changed_by_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    account: Mapped[AiAccount] = relationship(back_populates="plan_history")
    plan: Mapped[AiPlan] = relationship()


class UsageSummary(Base):
    __tablename__ = "usage_summaries"
    __table_args__ = (UniqueConstraint("account_id", "period", name="uq_usage_summary_account_period"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    latest_ingestion_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_ingestions.id"), nullable=True
    )
    sync_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True
    )
    primary_metric_value: Mapped[float] = mapped_column(Numeric(12, 4))
    primary_metric_unit: Mapped[str] = mapped_column(String(16))
    reported_spend_usd: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    estimated_included_spend_usd: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    estimation_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    unmatched_models: Mapped[list | None] = mapped_column(JSON, nullable=True)
    quota_usage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    billing_cycle_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    billing_cycle_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_id_used: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quota_denominator_snapshot: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    cycle_metric_value: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    cycle_quota_usage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    cursor_pools: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    external_models: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    shared_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    breakdown_by_model: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    account: Mapped[AiAccount] = relationship(back_populates="usage_summaries")


class AccountQuotaSnapshot(Base):
    __tablename__ = "account_quota_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    cycle_start: Mapped[date] = mapped_column(Date)
    cycle_end: Mapped[date] = mapped_column(Date)
    limit_cents: Mapped[int] = mapped_column(Integer, default=0)
    used_cents: Mapped[int] = mapped_column(Integer, default=0)
    remaining_cents: Mapped[int] = mapped_column(Integer, default=0)
    auto_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    api_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class KeyLoan(Base):
    __tablename__ = "key_loans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    credential_id: Mapped[str] = mapped_column(ForeignKey("ai_account_credentials.id"), index=True)
    borrower_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True, index=True
    )
    borrower_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_used_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    auto_revoke_on_reset: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # cursor_direct: 用户持 cr*；proxy_alias: 用户持 pka_，底层 cr* 仅服务端/管理员可见
    delivery_mode: Mapped[str] = mapped_column(String(32), default="cursor_direct")
    alias_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    alias_key_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    alias_encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class UsageDailyAggregate(Base):
    __tablename__ = "usage_daily_aggregates"
    __table_args__ = (
        UniqueConstraint("account_id", "event_date", "model", name="uq_daily_agg"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    model: Mapped[str] = mapped_column(String(128))
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    applicant_member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("ai_vendors.id"), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    manager_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True, index=True
    )
    decided_by_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_accounts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    applicant_member: Mapped[Member] = relationship(
        foreign_keys=[applicant_member_id],
    )
    vendor: Mapped[AiVendor] = relationship()


class CapabilityInvocationRow(Base):
    __tablename__ = "capability_invocations"
    __table_args__ = (
        UniqueConstraint("team_id", "idempotency_key", name="uq_capability_invocation_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    capability_key: Mapped[str] = mapped_column(String(128))
    capability_version: Mapped[str] = mapped_column(String(16))
    actor_member_id: Mapped[str] = mapped_column(String(36))
    request_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PortalChatDelivery(Base):
    __tablename__ = "portal_chat_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str] = mapped_column(String(36), index=True)
    assistant_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assistant_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="final")
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    author_member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("ai_vendors.id"), nullable=True)
    period: Mapped[str | None] = mapped_column(String(7), index=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    raw_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_channel: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="published")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    author_member: Mapped[Member | None] = relationship(foreign_keys=[author_member_id])
    vendor: Mapped[AiVendor | None] = relationship()


class ProxyKey(Base):
    __tablename__ = "proxy_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_hint: Mapped[str] = mapped_column(String(16))
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # unlimited | quota
    token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_5h_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|suspended|revoked
    suspended_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProxyKeyUsage(Base):
    __tablename__ = "proxy_key_usages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    proxy_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("proxy_keys.id"), nullable=True, index=True
    )
    loan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_input: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_output: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_cache_write: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_reasoning: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ProxyEvent(Base):
    __tablename__ = "proxy_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    # 不设 FK：事件需在 proxy key 删除后保留审计轨迹
    proxy_key_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    loan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
