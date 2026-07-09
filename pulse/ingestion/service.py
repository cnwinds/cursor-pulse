from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pulse.domain import CostRaw, UsageEventRecord
from pulse.ingestion.daily import rebuild_daily_aggregates
from pulse.ingestion.protocols import IngestionAdapter
from pulse.ingestion.types import IngestionContext, IngestionResult
from pulse.integrations.cursor_api import UsageEventDTO
from pulse.pricing.estimator import resolve_cost_fields
from pulse.storage.models import AiAccount, UsageIngestion, UsageRecord, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.usage import build_account_usage_summary


class UsageIngestionService:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id

    def ingest(
        self,
        *,
        context: IngestionContext,
        adapter: IngestionAdapter,
        status: str | None = None,
    ) -> IngestionResult:
        events = context.events or adapter.extract_events(context)
        metadata = context.metadata or adapter.extract_metadata(context)
        final_status = status or ("pending_review" if adapter.requires_review() else "confirmed")

        ingestion = UsageIngestion(
            member_id=context.member_id,
            account_id=context.account_id,
            vendor_id=context.vendor_id,
            billing_period=context.billing_period,
            source_type=context.source_type,
            channel=context.channel,
            status=final_status,
            triggered_by=context.triggered_by,
            event_count=len(events),
            metadata_json=metadata,
            raw_snapshot_path=str(context.raw_file_path) if context.raw_file_path else None,
            raw_text=context.raw_text,
            confirmed_at=datetime.now(timezone.utc) if final_status == "confirmed" else None,
        )
        self.session.add(ingestion)
        self.session.flush()

        if final_status == "confirmed":
            self._replace_account_period_records(
                context.account_id, context.billing_period, ingestion.id
            )

        member_id = self._resolve_member_id(context)
        for dto in events:
            self.session.add(self._to_usage_record(dto, ingestion.id, member_id))

        self.session.flush()
        records = list(
            self.session.scalars(
                select(UsageRecord).where(UsageRecord.ingestion_id == ingestion.id)
            )
        )
        if final_status == "confirmed":
            self._recompute_summary(context, ingestion, records)
            affected_dates = {dto.event_date for dto in events}
            rebuild_daily_aggregates(self.session, context.account_id, affected_dates)

        self.session.commit()
        return IngestionResult(
            ingestion_id=ingestion.id,
            event_count=len(events),
            status=final_status,
        )

    def _resolve_member_id(self, context: IngestionContext) -> str:
        if context.member_id:
            return context.member_id
        account = self.session.get(AiAccount, context.account_id)
        if account and account.primary_member_id:
            return account.primary_member_id
        if context.triggered_by and context.triggered_by != "system":
            return context.triggered_by
        raise ValueError("member_id required for usage records")

    def _replace_account_period_records(
        self, account_id: str, period: str, current_ingestion_id: str
    ) -> None:
        old_ingestions = self.session.scalars(
            select(UsageIngestion).where(
                UsageIngestion.account_id == account_id,
                UsageIngestion.billing_period == period,
                UsageIngestion.status == "confirmed",
                UsageIngestion.id != current_ingestion_id,
            )
        ).all()
        for ing in old_ingestions:
            self.session.execute(
                delete(UsageRecord).where(UsageRecord.ingestion_id == ing.id)
            )
            self.session.delete(ing)

    def _recompute_summary(
        self,
        context: IngestionContext,
        ingestion: UsageIngestion,
        records: list[UsageRecord],
    ) -> None:
        tool_repo = ToolCenterRepository(self.session, self.team_id)
        account = tool_repo.get_account(context.account_id)
        if not account:
            raise ValueError("账号不存在")
        plan = tool_repo.get_plan(account.plan_id)
        if not plan:
            raise ValueError("账号套餐不存在")

        summary = build_account_usage_summary(
            account=account,
            plan=plan,
            records=records,
            period=context.billing_period,
            plan_at_date=lambda d: tool_repo.resolve_plan_at_date(account.id, d),
        )
        sync_source = "api" if context.source_type == "api_sync" else "manual"
        now = datetime.now(timezone.utc)
        submitted_by = context.member_id or self._resolve_member_id(context)

        existing = self.session.scalar(
            select(UsageSummary).where(
                UsageSummary.account_id == context.account_id,
                UsageSummary.period == context.billing_period,
            )
        )
        if existing:
            existing.latest_ingestion_id = ingestion.id
            existing.sync_source = sync_source
            existing.last_synced_at = now
            existing.submitted_by_member_id = submitted_by
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
            existing.shared_note = account.shared_note
            existing.computed_at = now
            return

        self.session.add(
            UsageSummary(
                account_id=context.account_id,
                period=context.billing_period,
                latest_ingestion_id=ingestion.id,
                sync_source=sync_source,
                last_synced_at=now,
                submitted_by_member_id=submitted_by,
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
                shared_note=account.shared_note,
                breakdown_by_model=summary.get("breakdown_by_model"),
                computed_at=now,
            )
        )

    @staticmethod
    def _dto_to_event_record(dto: UsageEventDTO) -> UsageEventRecord:
        try:
            cost_raw = CostRaw(dto.cost_raw)
        except ValueError:
            cost_raw = CostRaw.USAGE_BASED
        return UsageEventRecord(
            event_at=dto.event_at,
            event_date=dto.event_date,
            kind=dto.kind,
            model=dto.model,
            max_mode=False,
            tokens_input_cache_write=dto.tokens_input_cache_write,
            tokens_input_no_cache=dto.tokens_input_no_cache,
            tokens_cache_read=dto.tokens_cache_read,
            tokens_output=dto.tokens_output,
            tokens_total=dto.tokens_total,
            cost_raw=cost_raw,
            cost_usd=Decimal(str(dto.cost_usd)),
            cloud_agent_id=None,
            automation_id=None,
            source_row_hash=dto.source_row_hash,
        )

    @staticmethod
    def _to_usage_record(
        dto: UsageEventDTO,
        ingestion_id: str,
        member_id: str,
        extraction_confidence: float = 1.0,
    ) -> UsageRecord:
        rec = UsageIngestionService._dto_to_event_record(dto)
        costs = resolve_cost_fields(rec)
        return UsageRecord(
            ingestion_id=ingestion_id,
            external_id=dto.external_id,
            member_id=member_id,
            event_at=rec.event_at,
            event_date=rec.event_date,
            kind=rec.kind,
            model=rec.model,
            max_mode=rec.max_mode,
            tokens_input_cache_write=rec.tokens_input_cache_write,
            tokens_input_no_cache=rec.tokens_input_no_cache,
            tokens_cache_read=rec.tokens_cache_read,
            tokens_output=rec.tokens_output,
            tokens_total=rec.tokens_total,
            cost_raw=rec.cost_raw.value,
            cost_usd=costs["cost_usd"],
            cost_estimated_usd=costs["cost_estimated_usd"],
            cost_basis=costs["cost_basis"],
            pricing_version=costs["pricing_version"],
            pricing_rule=costs["pricing_rule"],
            cloud_agent_id=rec.cloud_agent_id,
            automation_id=rec.automation_id,
            source_row_hash=rec.source_row_hash,
            extraction_confidence=extraction_confidence,
        )
