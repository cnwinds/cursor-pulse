from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync_errors import classify_sync_error
from pulse.ingestion.types import IngestionResult
from pulse.integrations.coding_plan import CodingPlanExtra, fetch_glm_quota, fetch_kimi_quota, fetch_minimax_quota
from pulse.tool_center.repository import ToolCenterRepository
from pulse.integrations.coding_plan.types import CodingPlanQuotaResult, QuotaTier
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, AiPlan
from pulse.tool_center.quota_reads import prune_quota_snapshots_for_account

logger = logging.getLogger(__name__)

CODING_PLAN_VENDORS = frozenset({"glm", "minimax", "kimi"})


def _parse_reset_datetime(iso: str | None) -> datetime | None:
    if not iso:
        return None
    text = iso.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _snapshot_cycles_from_tiers(tiers: list[QuotaTier], *, today: date) -> tuple[date, date, datetime | None]:
    reset_dts = [_parse_reset_datetime(t.resets_at) for t in tiers]
    reset_dts = [d for d in reset_dts if d is not None]
    if reset_dts:
        earliest = min(reset_dts, key=lambda d: d.timestamp())
        return today, earliest.date(), earliest
    fallback_end = today + timedelta(days=7)
    return today, fallback_end, None


def _max_utilization(tiers: list[QuotaTier]) -> float | None:
    if not tiers:
        return None
    return max(t.utilization_pct for t in tiers)


def _apply_glm_plan_from_level(
    session: Session,
    account: AiAccount,
    level: str | None,
    *,
    member_id: str | None = None,
) -> None:
    if not level or not account.team_id:
        return
    slug = level.strip().lower()
    if not slug:
        return
    vendor_id = account.vendor_id
    plan = session.scalar(
        select(AiPlan).where(
            AiPlan.vendor_id == vendor_id,
            AiPlan.slug == slug,
        )
    )
    if plan and account.plan_id != plan.id:
        repo = ToolCenterRepository(session, account.team_id)
        repo.change_account_plan(
            account.id,
            new_plan_id=plan.id,
            effective_from=date.today(),
            changed_by_member_id=member_id,
            note=f"GLM API level={slug}",
        )


def _write_snapshot(
    session: Session,
    account: AiAccount,
    quota: CodingPlanQuotaResult,
    *,
    captured_at: datetime | None = None,
    member_id: str | None = None,
) -> AccountQuotaSnapshot:
    now = captured_at or datetime.now(UTC)
    today = now.date()
    tiers = quota.tiers
    max_pct = _max_utilization(tiers)
    cycle_start, cycle_end, cycle_end_at = _snapshot_cycles_from_tiers(tiers, today=today)

    extra = CodingPlanExtra(
        schema_version=1,
        plan_level=quota.plan_level,
        tiers=tiers,
        extras=quota.extras,
    )

    if account.vendor and account.vendor.slug == "glm":
        _apply_glm_plan_from_level(session, account, quota.plan_level, member_id=member_id)

    weekly = next((t for t in tiers if t.name == "weekly_limit"), None)
    if weekly and weekly.resets_at:
        reset_dt = _parse_reset_datetime(weekly.resets_at)
        if reset_dt and account.resets_on_source != "manual-locked":
            account.usage_resets_on = reset_dt.date()
            account.resets_on_source = "api"

    snapshot = AccountQuotaSnapshot(
        account_id=account.id,
        captured_at=now,
        sync_kind="coding_plan",
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        cycle_start_at=None,
        cycle_end_at=cycle_end_at,
        limit_cents=0,
        used_cents=0,
        remaining_cents=0,
        auto_pct=None,
        api_pct=None,
        total_pct=max_pct,
        quota_extra=extra.to_json(),
    )
    session.add(snapshot)
    session.flush()
    prune_quota_snapshots_for_account(session, account.id)
    return snapshot


class CodingPlanQuotaSyncService:
    def __init__(self, session: Session, encryption_key: str):
        self.session = session
        self.encryption_key = encryption_key
        self.credential_service = CredentialService(session, encryption_key)

    def _fetch_quota(self, account: AiAccount, api_key: str) -> CodingPlanQuotaResult:
        slug = account.vendor.slug if account.vendor else ""
        region = (account.api_region or "").strip()
        if slug == "glm":
            if region not in ("zai", "bigmodel"):
                raise ValueError("GLM 账号须配置 api_region（zai 或 bigmodel）")
            return fetch_glm_quota(
                api_key,
                region=region,
                organization_id=account.glm_organization_id,
                project_id=account.glm_project_id,
            )
        if slug == "minimax":
            if region not in ("cn", "global"):
                raise ValueError("MiniMax 账号须配置 api_region（cn 或 global）")
            return fetch_minimax_quota(api_key, region=region)
        if slug == "kimi":
            return fetch_kimi_quota(api_key)
        raise ValueError(f"unsupported coding plan vendor: {slug}")

    def sync_account(
        self,
        account_id: str,
        *,
        channel: str = "scheduler",
        member_id: str | None = None,
    ) -> IngestionResult:
        _ = channel, member_id
        cred = self.credential_service.get_primary_credential(account_id)
        if not cred or not cred.sync_enabled:
            raise ValueError("no active credential")

        account = self.session.scalar(
            select(AiAccount)
            .options(joinedload(AiAccount.vendor), joinedload(AiAccount.plan))
            .where(AiAccount.id == account_id)
        )
        if not account or not account.vendor:
            raise ValueError("account not found")
        if account.vendor.slug not in CODING_PLAN_VENDORS:
            raise ValueError("not a coding plan account")

        try:
            api_key = self.credential_service.decrypt_api_key(cred)
            quota = self._fetch_quota(account, api_key)
            now = datetime.now(UTC)
            _write_snapshot(self.session, account, quota, captured_at=now, member_id=member_id)

            cred.last_sync_at = now
            cred.last_sync_status = "success"
            cred.last_sync_error = None
            self.session.commit()
            return IngestionResult(ingestion_id="", event_count=0, status="confirmed")
        except Exception as exc:
            classified = classify_sync_error(exc)
            self.session.rollback()
            cred = self.credential_service.get_primary_credential(account_id)
            if cred:
                cred.last_sync_status = "failed"
                cred.last_sync_error = str(classified)
                self.session.commit()
            raise classified from exc
