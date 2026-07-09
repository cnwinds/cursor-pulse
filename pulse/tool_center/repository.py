from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, joinedload

from pulse.storage.models import (
    AiAccount,
    AiAccountMember,
    AiAccountPlanHistory,
    AiPlan,
    AiVendor,
    Member,
    UsageIngestion,
    UsageSummary,
)
from pulse.tool_center.billing_cycle import add_months

_ACCOUNT_ACTIVE_STATUSES = frozenset({"trial", "shared", "dedicated"})


class ToolCenterRepository:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id

    def list_vendors(self, *, active_only: bool = True) -> list[AiVendor]:
        query = select(AiVendor)
        if active_only:
            query = query.where(AiVendor.is_active.is_(True))
        return list(self.session.scalars(query.order_by(AiVendor.name)))

    def get_vendor_by_slug(self, slug: str) -> AiVendor | None:
        return self.session.scalar(select(AiVendor).where(AiVendor.slug == slug))

    def list_plans(self, vendor_id: str | None = None) -> list[AiPlan]:
        query = select(AiPlan).options(joinedload(AiPlan.vendor))
        if vendor_id:
            query = query.where(AiPlan.vendor_id == vendor_id)
        return list(self.session.scalars(query.order_by(AiPlan.plan_name)))

    def get_plan(self, plan_id: str) -> AiPlan | None:
        return self.session.get(AiPlan, plan_id)

    def list_accounts(self, *, status: str | None = None) -> list[AiAccount]:
        query = (
            select(AiAccount)
            .options(joinedload(AiAccount.plan), joinedload(AiAccount.vendor))
            .where(AiAccount.team_id == self.team_id)
        )
        if status:
            query = query.where(AiAccount.status == status)
        return list(self.session.scalars(query.order_by(AiAccount.account_identifier)))

    def list_active_accounts(self) -> list[AiAccount]:
        return [
            account
            for account in self.list_accounts()
            if account.status in _ACCOUNT_ACTIVE_STATUSES
        ]

    def get_account(self, account_id: str) -> AiAccount | None:
        account = self.session.scalar(
            select(AiAccount)
            .options(
                joinedload(AiAccount.plan),
                joinedload(AiAccount.vendor),
                joinedload(AiAccount.secondary_members),
            )
            .where(AiAccount.id == account_id, AiAccount.team_id == self.team_id)
        )
        return account

    def create_account(
        self,
        *,
        vendor_id: str,
        plan_id: str,
        account_identifier: str,
        status: str = "shared",
        primary_member_id: str | None = None,
        shared_note: str | None = None,
        ownership: str = "company",
        usage_resets_on: date | None = None,
    ) -> AiAccount:
        now = datetime.now(timezone.utc)
        account = AiAccount(
            team_id=self.team_id,
            vendor_id=vendor_id,
            plan_id=plan_id,
            account_identifier=account_identifier,
            status=status,
            primary_member_id=primary_member_id,
            shared_note=shared_note,
            ownership=ownership,
            usage_resets_on=usage_resets_on,
            created_at=now,
            updated_at=now,
        )
        self.session.add(account)
        self.session.flush()
        self.record_initial_plan_history(account)
        return account

    def record_initial_plan_history(
        self,
        account: AiAccount,
        *,
        effective_from: date | None = None,
    ) -> None:
        existing = self.session.scalar(
            select(AiAccountPlanHistory.id).where(
                AiAccountPlanHistory.account_id == account.id
            ).limit(1)
        )
        if existing:
            return
        eff = effective_from or account.started_on or date.today()
        self.session.add(
            AiAccountPlanHistory(
                account_id=account.id,
                plan_id=account.plan_id,
                effective_from=eff,
            )
        )
        self.session.flush()

    def change_account_plan(
        self,
        account_id: str,
        *,
        new_plan_id: str,
        effective_from: date,
        changed_by_member_id: str | None = None,
        note: str | None = None,
    ) -> AiAccount:
        account = self.get_account(account_id)
        if not account:
            raise ValueError("账号不存在")
        if account.plan_id == new_plan_id:
            return account

        open_row = self.session.scalar(
            select(AiAccountPlanHistory).where(
                AiAccountPlanHistory.account_id == account_id,
                AiAccountPlanHistory.effective_to.is_(None),
            )
        )
        if open_row:
            open_row.effective_to = effective_from
        else:
            self.record_initial_plan_history(
                account,
                effective_from=min(effective_from, account.started_on or effective_from),
            )

        self.session.add(
            AiAccountPlanHistory(
                account_id=account_id,
                plan_id=new_plan_id,
                effective_from=effective_from,
                changed_by_member_id=changed_by_member_id,
                note=note,
            )
        )
        account.plan_id = new_plan_id
        account.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return account

    def backfill_plan_upgrade(
        self,
        account_id: str,
        *,
        previous_plan_id: str,
        effective_from: date,
        changed_by_member_id: str | None = None,
        note: str | None = None,
    ) -> AiAccount:
        """为已升级账号补录 Pro→Pro+ 等历史（当前 plan_id 已是新套餐）。"""
        account = self.get_account(account_id)
        if not account:
            raise ValueError("账号不存在")

        existing = list(
            self.session.scalars(
                select(AiAccountPlanHistory).where(
                    AiAccountPlanHistory.account_id == account_id
                )
            )
        )
        if len(existing) > 1:
            raise ValueError("已有完整套餐历史，无需补录")
        for row in existing:
            self.session.delete(row)

        prev_start = account.started_on or add_months(effective_from, -1)
        self.session.add(
            AiAccountPlanHistory(
                account_id=account_id,
                plan_id=previous_plan_id,
                effective_from=prev_start,
                effective_to=effective_from,
                note=note,
            )
        )
        self.session.add(
            AiAccountPlanHistory(
                account_id=account_id,
                plan_id=account.plan_id,
                effective_from=effective_from,
                changed_by_member_id=changed_by_member_id,
                note=note,
            )
        )
        account.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return account

    def recompute_usage_summary(self, account_id: str, period: str) -> UsageSummary | None:
        from pulse.storage.models import UsageRecord

        account = self.get_account(account_id)
        if not account:
            return None
        summary_row = self.session.scalar(
            select(UsageSummary).where(
                UsageSummary.account_id == account_id,
                UsageSummary.period == period,
            )
        )
        if not summary_row or not summary_row.latest_ingestion_id:
            return None
        records = list(
            self.session.scalars(
                select(UsageRecord).where(
                    UsageRecord.ingestion_id == summary_row.latest_ingestion_id
                )
            )
        )
        if not records:
            return None
        summary = self.build_summary_for_account(account, records, period)
        return self.upsert_usage_summary(
            account_id=account_id,
            period=period,
            ingestion_id=summary_row.latest_ingestion_id,
            submitted_by_member_id=summary_row.submitted_by_member_id or "",
            summary=summary,
            shared_note=account.shared_note,
        )

    def resolve_plan_at_date(self, account_id: str, on_date: date) -> AiPlan | None:
        row = self.session.scalar(
            select(AiAccountPlanHistory)
            .options(joinedload(AiAccountPlanHistory.plan))
            .where(
                AiAccountPlanHistory.account_id == account_id,
                AiAccountPlanHistory.effective_from <= on_date,
                or_(
                    AiAccountPlanHistory.effective_to.is_(None),
                    AiAccountPlanHistory.effective_to > on_date,
                ),
            )
            .order_by(AiAccountPlanHistory.effective_from.desc())
            .limit(1)
        )
        if row and row.plan:
            return row.plan
        account = self.get_account(account_id)
        if account:
            return self.get_plan(account.plan_id)
        return None

    def build_summary_for_account(
        self,
        account: AiAccount,
        records: list,
        period: str,
    ) -> dict:
        from pulse.tool_center.usage import build_account_usage_summary

        plan = self.get_plan(account.plan_id)
        if not plan:
            raise ValueError("账号套餐不存在")
        return build_account_usage_summary(
            account=account,
            plan=plan,
            records=records,
            period=period,
            plan_at_date=lambda d: self.resolve_plan_at_date(account.id, d),
        )

    def update_account(self, account_id: str, **fields) -> AiAccount:
        account = self.get_account(account_id)
        if not account:
            raise ValueError("账号不存在")
        for key, value in fields.items():
            if hasattr(account, key):
                setattr(account, key, value)
        account.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return account

    def set_secondary_members(self, account_id: str, member_ids: list[str]) -> None:
        account = self.get_account(account_id)
        if not account:
            raise ValueError("账号不存在")
        self.session.execute(
            delete(AiAccountMember).where(AiAccountMember.account_id == account_id)
        )
        for member_id in member_ids:
            self.session.add(
                AiAccountMember(account_id=account_id, member_id=member_id, role="secondary")
            )
        self.session.flush()

    def get_submitted_account_ids(self, period: str) -> set[str]:
        rows = self.session.scalars(
            select(UsageSummary.account_id).where(UsageSummary.period == period)
        )
        return set(rows)

    def get_unsubmitted_accounts(self, period: str) -> list[AiAccount]:
        submitted = self.get_submitted_account_ids(period)
        return [a for a in self.list_active_accounts() if a.id not in submitted]

    def accounts_missing_primary(self) -> list[AiAccount]:
        return [a for a in self.list_active_accounts() if not a.primary_member_id]

    def get_primary_accounts_for_member(self, member_id: str) -> list[AiAccount]:
        return list(
            self.session.scalars(
                select(AiAccount)
                .options(joinedload(AiAccount.vendor), joinedload(AiAccount.plan))
                .where(
                    AiAccount.team_id == self.team_id,
                    AiAccount.primary_member_id == member_id,
                    AiAccount.status.in_(_ACCOUNT_ACTIVE_STATUSES),
                )
            )
        )

    def upsert_usage_summary(
        self,
        *,
        account_id: str,
        period: str,
        ingestion_id: str,
        submitted_by_member_id: str,
        summary: dict,
        shared_note: str | None = None,
    ) -> UsageSummary:
        existing = self.session.scalar(
            select(UsageSummary).where(
                UsageSummary.account_id == account_id,
                UsageSummary.period == period,
            )
        )
        now = datetime.now(timezone.utc)
        if existing:
            existing.latest_ingestion_id = ingestion_id
            existing.submitted_by_member_id = submitted_by_member_id
            existing.primary_metric_value = summary["primary_metric_value"]
            existing.primary_metric_unit = summary["primary_metric_unit"]
            existing.quota_usage_ratio = summary.get("quota_usage_ratio")
            existing.breakdown_by_model = summary.get("breakdown_by_model")
            existing.reported_spend_usd = summary.get("reported_spend_usd")
            existing.estimated_included_spend_usd = summary.get("estimated_included_spend_usd")
            existing.estimation_coverage_pct = summary.get("estimation_coverage_pct")
            existing.unmatched_models = summary.get("unmatched_models")
            existing.billing_cycle_start = summary.get("billing_cycle_start")
            existing.billing_cycle_end = summary.get("billing_cycle_end")
            existing.plan_id_used = summary.get("plan_id_used")
            existing.quota_denominator_snapshot = summary.get("quota_denominator_snapshot")
            existing.cycle_metric_value = summary.get("cycle_metric_value")
            existing.cycle_quota_usage_ratio = summary.get("cycle_quota_usage_ratio")
            existing.cursor_pools = summary.get("cursor_pools")
            existing.external_models = summary.get("external_models")
            existing.shared_note = shared_note
            existing.computed_at = now
            self.session.flush()
            return existing

        row = UsageSummary(
            account_id=account_id,
            period=period,
            latest_ingestion_id=ingestion_id,
            submitted_by_member_id=submitted_by_member_id,
            primary_metric_value=summary["primary_metric_value"],
            primary_metric_unit=summary["primary_metric_unit"],
            quota_usage_ratio=summary.get("quota_usage_ratio"),
            reported_spend_usd=summary.get("reported_spend_usd"),
            estimated_included_spend_usd=summary.get("estimated_included_spend_usd"),
            estimation_coverage_pct=summary.get("estimation_coverage_pct"),
            unmatched_models=summary.get("unmatched_models"),
            billing_cycle_start=summary.get("billing_cycle_start"),
            billing_cycle_end=summary.get("billing_cycle_end"),
            plan_id_used=summary.get("plan_id_used"),
            quota_denominator_snapshot=summary.get("quota_denominator_snapshot"),
            cycle_metric_value=summary.get("cycle_metric_value"),
            cycle_quota_usage_ratio=summary.get("cycle_quota_usage_ratio"),
            cursor_pools=summary.get("cursor_pools"),
            external_models=summary.get("external_models"),
            shared_note=shared_note,
            breakdown_by_model=summary.get("breakdown_by_model"),
            computed_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def delete_account_period_ingestions(self, account_id: str, period: str) -> None:
        from pulse.storage.models import UsageRecord

        old_ingestions = self.session.scalars(
            select(UsageIngestion).where(
                UsageIngestion.account_id == account_id,
                UsageIngestion.billing_period == period,
                UsageIngestion.status == "confirmed",
            )
        ).all()
        for ing in old_ingestions:
            self.session.execute(delete(UsageRecord).where(UsageRecord.ingestion_id == ing.id))
            self.session.delete(ing)
        self.session.execute(
            delete(UsageSummary).where(
                UsageSummary.account_id == account_id,
                UsageSummary.period == period,
            )
        )
