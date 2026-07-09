from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pulse.domain import ParsedCsv, UsageEventRecord
from pulse.extract.period_split import split_parsed_by_period
from pulse.ingestion.adapters.manual_csv import ManualCsvAdapter, _record_to_dto
from pulse.ingestion.registry import resolve_adapter
from pulse.ingestion.service import UsageIngestionService
from pulse.ingestion.types import IngestionContext
from pulse.pricing.estimator import resolve_cost_fields
from pulse.storage.models import Member, UsageIngestion, UsageRecord
from pulse.tool_center.account_pick import filter_cursor_accounts
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.submission_status import period_date_range
from pulse.tool_center.upgrade import notify_upgrade_if_needed

INPUT_TYPE_TO_SOURCE_TYPE: dict[str, str] = {
    "csv": "manual_csv",
    "screenshot": "manual_vision",
    "manual": "manual_text",
    "text": "manual_text",
    "api": "api_sync",
}


def source_type_from_input_type(input_type: str) -> str:
    return INPUT_TYPE_TO_SOURCE_TYPE.get(input_type, input_type)


def input_type_from_source_type(source_type: str) -> str:
    mapping = {
        "manual_csv": "csv",
        "manual_vision": "screenshot",
        "manual_text": "manual",
        "api_sync": "api",
    }
    return mapping.get(source_type, source_type)


class Repository:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id

    def get_member_by_dingtalk_id(self, dingtalk_user_id: str) -> Member | None:
        return self.session.scalar(
            select(Member).where(
                Member.team_id == self.team_id,
                Member.dingtalk_user_id == dingtalk_user_id,
            )
        )

    def get_or_create_member(self, dingtalk_user_id: str, display_name: str) -> Member:
        member = self.get_member_by_dingtalk_id(dingtalk_user_id)
        if member:
            if display_name and member.display_name != display_name:
                member.display_name = display_name
            return member
        member = Member(
            team_id=self.team_id,
            dingtalk_user_id=dingtalk_user_id,
            display_name=display_name or dingtalk_user_id,
            status="pending",
        )
        self.session.add(member)
        self.session.flush()
        return member

    def list_active_members(self) -> list[Member]:
        return list(
            self.session.scalars(
                select(Member).where(Member.team_id == self.team_id, Member.status == "active")
            )
        )

    def add_member(self, dingtalk_user_id: str, display_name: str) -> Member:
        member = self.get_or_create_member(dingtalk_user_id, display_name)
        member.status = "active"
        return member

    def get_submitted_member_ids(self, period: str) -> set[str]:
        rows = self.session.scalars(
            select(UsageIngestion.member_id)
            .join(Member)
            .where(
                Member.team_id == self.team_id,
                UsageIngestion.billing_period == period,
                UsageIngestion.status == "confirmed",
            )
        )
        return {mid for mid in rows if mid}

    def get_unsubmitted_members(self, period: str) -> list[Member]:
        submitted = self.get_submitted_member_ids(period)
        return [m for m in self.list_active_members() if m.id not in submitted]

    def _delete_confirmed_period_records(self, member_id: str, period: str) -> None:
        old_ingestions = self.session.scalars(
            select(UsageIngestion).where(
                UsageIngestion.member_id == member_id,
                UsageIngestion.billing_period == period,
                UsageIngestion.status == "confirmed",
                UsageIngestion.account_id.is_(None),
            )
        ).all()
        for ing in old_ingestions:
            self.session.execute(delete(UsageRecord).where(UsageRecord.ingestion_id == ing.id))
            self.session.delete(ing)

    def _tool_repo(self) -> ToolCenterRepository:
        return ToolCenterRepository(self.session, self.team_id)

    def _ingestion_service(self) -> UsageIngestionService:
        return UsageIngestionService(self.session, self.team_id)

    def list_pending_ingestions(self, period: str | None = None) -> list[UsageIngestion]:
        query = (
            select(UsageIngestion)
            .join(Member)
            .where(Member.team_id == self.team_id, UsageIngestion.status == "pending_review")
        )
        if period:
            query = query.where(UsageIngestion.billing_period == period)
        return list(self.session.scalars(query.order_by(UsageIngestion.ingested_at.desc())))

    def find_ingestion_by_id_prefix(self, prefix: str) -> UsageIngestion | None:
        rows = list(
            self.session.scalars(
                select(UsageIngestion)
                .join(Member)
                .where(
                    Member.team_id == self.team_id,
                    UsageIngestion.id.like(f"{prefix}%"),
                )
            )
        )
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise ValueError(f"摄取 ID 前缀 {prefix!r} 不唯一，请提供更多字符")
        return None

    def confirm_ingestion(self, ingestion_id: str) -> UsageIngestion:
        ing = self.session.get(UsageIngestion, ingestion_id)
        if not ing or ing.status != "pending_review":
            raise ValueError("未找到待审摄取或状态不正确")
        member = self.session.get(Member, ing.member_id) if ing.member_id else None
        if not member or member.team_id != self.team_id:
            raise ValueError("无权操作该摄取")

        tool_repo = self._tool_repo()
        if ing.account_id:
            from pulse.storage.models import UsageSummary

            old_ingestions = self.session.scalars(
                select(UsageIngestion).where(
                    UsageIngestion.account_id == ing.account_id,
                    UsageIngestion.billing_period == ing.billing_period,
                    UsageIngestion.status == "confirmed",
                    UsageIngestion.id != ing.id,
                )
            ).all()
            for old in old_ingestions:
                self.session.execute(
                    delete(UsageRecord).where(UsageRecord.ingestion_id == old.id)
                )
                self.session.delete(old)
            self.session.execute(
                delete(UsageSummary).where(
                    UsageSummary.account_id == ing.account_id,
                    UsageSummary.period == ing.billing_period,
                )
            )
        elif ing.member_id:
            self._delete_confirmed_period_records(ing.member_id, ing.billing_period)

        ing.status = "confirmed"
        ing.confirmed_at = datetime.now(timezone.utc)
        self.session.flush()

        if ing.account_id:
            account = tool_repo.get_account(ing.account_id)
            if account:
                records = list(
                    self.session.scalars(
                        select(UsageRecord).where(UsageRecord.ingestion_id == ing.id)
                    )
                )
                if records:
                    summary = tool_repo.build_summary_for_account(
                        account, records, ing.billing_period
                    )
                    tool_repo.upsert_usage_summary(
                        account_id=ing.account_id,
                        period=ing.billing_period,
                        ingestion_id=ing.id,
                        submitted_by_member_id=ing.member_id or member.id,
                        summary=summary,
                        shared_note=account.shared_note if account else None,
                    )
                elif ing.metadata_json and ing.source_type in ("manual_text", "manual_vision"):
                    tool_repo.upsert_usage_summary(
                        account_id=ing.account_id,
                        period=ing.billing_period,
                        ingestion_id=ing.id,
                        submitted_by_member_id=ing.member_id or member.id,
                        summary=ing.metadata_json,
                        shared_note=account.shared_note,
                    )

        self.session.flush()
        return ing

    def reject_ingestion(self, ingestion_id: str) -> None:
        ing = self.session.get(UsageIngestion, ingestion_id)
        if not ing or ing.status != "pending_review":
            raise ValueError("未找到待审摄取或状态不正确")
        member = self.session.get(Member, ing.member_id) if ing.member_id else None
        if not member or member.team_id != self.team_id:
            raise ValueError("无权操作该摄取")
        self.session.execute(delete(UsageRecord).where(UsageRecord.ingestion_id == ing.id))
        self.session.delete(ing)
        self.session.flush()

    def save_csv_ingestion(
        self,
        *,
        member: Member,
        period: str,
        parsed: ParsedCsv,
        submit_channel: str,
        raw_source: Path | None = None,
        raw_files_dir: Path | None = None,
        raw_text: str | None = None,
        input_type: str = "csv",
        upgrade_notify: tuple | None = None,
    ) -> UsageIngestion:
        return self.save_ingestion(
            member=member,
            period=period,
            parsed=parsed,
            submit_channel=submit_channel,
            input_type=input_type,
            raw_source=raw_source,
            raw_files_dir=raw_files_dir,
            raw_text=raw_text,
            upgrade_notify=upgrade_notify,
        )

    def save_split_ingestions(
        self,
        *,
        member: Member,
        parsed: ParsedCsv,
        submit_channel: str,
        default_period: str | None = None,
        input_type: str = "csv",
        raw_source: Path | None = None,
        raw_files_dir: Path | None = None,
        raw_text: str | None = None,
        extraction_confidence: float = 1.0,
        status: str = "confirmed",
        object_storage_config=None,
        team_slug: str = "default",
        account_id: str | None = None,
        upgrade_notify: tuple | None = None,
        allow_proxy: bool = False,
    ) -> list[tuple[str, UsageIngestion]]:
        splits = split_parsed_by_period(parsed)
        results: list[tuple[str, UsageIngestion]] = []
        for index, (period, partial) in enumerate(splits.items()):
            ingestion = self.save_ingestion(
                member=member,
                period=period,
                parsed=partial,
                submit_channel=submit_channel,
                input_type=input_type,
                raw_source=raw_source if index == 0 else None,
                raw_files_dir=raw_files_dir if index == 0 and raw_source else None,
                raw_text=raw_text if index == 0 else None,
                extraction_confidence=extraction_confidence,
                status=status,
                object_storage_config=object_storage_config,
                team_slug=team_slug,
                account_id=account_id,
                upgrade_notify=upgrade_notify,
                allow_proxy=allow_proxy,
            )
            results.append((period, ingestion))

        if default_period and default_period not in splits and status == "confirmed":
            period_start, period_end = period_date_range(default_period)
            if parsed.summary.date_max < period_start or parsed.summary.date_min > period_end:
                if account_id:
                    tool_repo = self._tool_repo()
                    tool_repo.delete_account_period_ingestions(account_id, default_period)
                else:
                    tool_repo = self._tool_repo()
                    primary_accounts = tool_repo.get_primary_accounts_for_member(member.id)
                    if len(primary_accounts) == 1:
                        tool_repo.delete_account_period_ingestions(
                            primary_accounts[0].id, default_period
                        )
                    else:
                        self._delete_confirmed_period_records(member.id, default_period)

        return results

    def save_ingestion(
        self,
        *,
        member: Member,
        period: str,
        parsed: ParsedCsv,
        submit_channel: str,
        input_type: str = "csv",
        raw_source: Path | None = None,
        raw_files_dir: Path | None = None,
        raw_text: str | None = None,
        extraction_confidence: float = 1.0,
        status: str = "confirmed",
        object_storage_config=None,
        team_slug: str = "default",
        account_id: str | None = None,
        upgrade_notify: tuple | None = None,
        allow_proxy: bool = False,
    ) -> UsageIngestion:
        tool_repo = self._tool_repo()
        account = None
        if account_id:
            account = tool_repo.get_account(account_id)
            if not account:
                raise ValueError("账号不存在或无权访问")
            if (
                account.primary_member_id
                and account.primary_member_id != member.id
                and not allow_proxy
            ):
                raise ValueError("仅账号主使用人可提交用量")
        elif input_type in ("csv", "text"):
            cursor_accounts = filter_cursor_accounts(
                tool_repo.get_primary_accounts_for_member(member.id)
            )
            if len(cursor_accounts) > 1:
                raise ValueError("有多个 Cursor 账号，请先指定账号后再提交")
            if len(cursor_accounts) == 1:
                account = cursor_accounts[0]
                account_id = account.id
        else:
            primary_accounts = tool_repo.get_primary_accounts_for_member(member.id)
            if len(primary_accounts) == 1:
                account = primary_accounts[0]
                account_id = account.id

        raw_file_path = self._archive_raw_file(
            member=member,
            period=period,
            raw_source=raw_source,
            raw_files_dir=raw_files_dir,
            object_storage_config=object_storage_config,
            team_slug=team_slug,
        )

        source_type = source_type_from_input_type(input_type)
        events = [_record_to_dto(rec) for rec in parsed.records]

        if account_id and account:
            context = IngestionContext(
                account_id=account_id,
                vendor_id=account.vendor_id or "",
                vendor_slug=account.vendor.slug if account.vendor else "cursor",
                billing_period=period,
                member_id=member.id,
                channel=submit_channel,
                source_type=source_type,
                triggered_by=member.id,
                raw_file_path=Path(raw_file_path) if raw_file_path else None,
                raw_text=raw_text,
                events=events,
            )
            adapter = resolve_adapter(context)
            result = self._ingestion_service().ingest(
                context=context,
                adapter=adapter,
                status=status,
                commit=False,
            )
            ingestion = self.session.get(UsageIngestion, result.ingestion_id)
            if ingestion and upgrade_notify and status == "confirmed":
                send_fn, admin_ids = upgrade_notify
                notify_upgrade_if_needed(
                    self.session,
                    account_id,
                    period,
                    send_private_message=send_fn,
                    admin_ids=admin_ids,
                )
            assert ingestion is not None
            return ingestion

        if status == "confirmed":
            self._delete_confirmed_period_records(member.id, period)

        now = datetime.now(timezone.utc)
        ingestion = UsageIngestion(
            member_id=member.id,
            account_id=account_id,
            vendor_id=account.vendor_id if account else None,
            billing_period=period,
            source_type=source_type,
            channel=submit_channel,
            status=status,
            triggered_by=member.id,
            event_count=len(parsed.records),
            raw_snapshot_path=raw_file_path,
            raw_text=raw_text,
            confirmed_at=now if status == "confirmed" else None,
        )
        self.session.add(ingestion)
        self.session.flush()

        for rec in parsed.records:
            self.session.add(
                self._to_usage_record(rec, ingestion.id, member.id, extraction_confidence)
            )
        self.session.flush()
        return ingestion

    def save_manual_ingestion(
        self,
        *,
        member: Member,
        period: str,
        account_id: str,
        summary: dict,
        submit_channel: str,
        raw_text: str | None = None,
        raw_source: Path | None = None,
        raw_files_dir: Path | None = None,
        extraction_confidence: float = 1.0,
        status: str = "confirmed",
        object_storage_config=None,
        team_slug: str = "default",
        upgrade_notify: tuple | None = None,
        source_type: str = "manual_text",
    ) -> UsageIngestion:
        tool_repo = self._tool_repo()
        account = tool_repo.get_account(account_id)
        if not account:
            raise ValueError("账号不存在或无权访问")

        if status == "confirmed":
            tool_repo.delete_account_period_ingestions(account_id, period)

        raw_file_path = self._archive_raw_file(
            member=member,
            period=period,
            raw_source=raw_source,
            raw_files_dir=raw_files_dir,
            object_storage_config=object_storage_config,
            team_slug=team_slug,
        )

        now = datetime.now(timezone.utc)
        ingestion = UsageIngestion(
            member_id=member.id,
            account_id=account_id,
            vendor_id=account.vendor_id,
            billing_period=period,
            source_type=source_type,
            channel=submit_channel,
            status=status,
            triggered_by=member.id,
            event_count=0,
            raw_snapshot_path=raw_file_path,
            raw_text=raw_text,
            metadata_json=summary,
            confirmed_at=now if status == "confirmed" else None,
        )
        self.session.add(ingestion)
        self.session.flush()

        if status == "confirmed":
            tool_repo.upsert_usage_summary(
                account_id=account_id,
                period=period,
                ingestion_id=ingestion.id,
                submitted_by_member_id=member.id,
                summary=summary,
                shared_note=account.shared_note,
            )
            if upgrade_notify:
                send_fn, admin_ids = upgrade_notify
                notify_upgrade_if_needed(
                    self.session,
                    account_id,
                    period,
                    send_private_message=send_fn,
                    admin_ids=admin_ids,
                )

        self.session.flush()
        return ingestion

    def _archive_raw_file(
        self,
        *,
        member: Member,
        period: str,
        raw_source: Path | None,
        raw_files_dir: Path | None,
        object_storage_config,
        team_slug: str,
    ) -> str | None:
        if not raw_source or not raw_files_dir:
            return None
        raw_files_dir.mkdir(parents=True, exist_ok=True)
        dest = raw_files_dir / f"{member.id}_{period}_{raw_source.name}"
        shutil.copy2(raw_source, dest)
        raw_file_path: str | None = str(dest)
        if object_storage_config and object_storage_config.enabled:
            from pulse.storage.object_store import archive_raw_file

            uri = archive_raw_file(
                object_storage_config,
                dest,
                team_slug=team_slug,
                member_id=member.id,
                period=period,
                filename=raw_source.name,
            )
            if uri:
                raw_file_path = uri
        return raw_file_path

    @staticmethod
    def _to_usage_record(
        rec: UsageEventRecord,
        ingestion_id: str,
        member_id: str,
        extraction_confidence: float = 1.0,
    ) -> UsageRecord:
        costs = resolve_cost_fields(rec)
        return UsageRecord(
            ingestion_id=ingestion_id,
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

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
