from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from pulse.storage.models import AiAccount, Member, UsageIngestion, UsageRecord
from pulse.tool_center.repository import ToolCenterRepository

INPUT_TYPE_TO_SOURCE_TYPE: dict[str, str] = {
    "api": "api_sync",
}


def source_type_from_input_type(input_type: str) -> str:
    return INPUT_TYPE_TO_SOURCE_TYPE.get(input_type, input_type)


def input_type_from_source_type(source_type: str) -> str:
    mapping = {
        "api_sync": "api",
        # Legacy manual source types (read-only display)
        "manual_csv": "csv",
        "manual_vision": "screenshot",
        "manual_text": "manual",
    }
    return mapping.get(source_type, source_type)


class Repository:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id

    def get_member_by_channel_user_id(
        self, channel_user_id: str, *, channel: str | None = None
    ) -> Member | None:
        from pulse.identity.service import resolve_member

        if channel is not None:
            return resolve_member(
                self.session,
                self.team_id,
                channel=channel,
                external_id=channel_user_id,
            )
        for preferred in ("web", "dingtalk", "feishu"):
            member = resolve_member(
                self.session,
                self.team_id,
                channel=preferred,
                external_id=channel_user_id,
            )
            if member is not None:
                return member
        return self.session.scalar(
            select(Member).where(
                Member.team_id == self.team_id,
                Member.channel_user_id == channel_user_id,
            )
        )

    def get_or_create_member(
        self,
        channel_user_id: str,
        display_name: str,
        *,
        channel: str = "dingtalk",
    ) -> Member:
        from pulse.identity.service import ensure_identity, refresh_member_primary_cache

        member = self.get_member_by_channel_user_id(channel_user_id, channel=channel)
        if member:
            ensure_identity(
                self.session, member, channel=channel, external_id=channel_user_id
            )
            if display_name and member.display_name != display_name:
                if (
                    display_name == channel_user_id
                    and member.display_name != channel_user_id
                ):
                    return member
                member.display_name = display_name
            return member
        member = Member(
            team_id=self.team_id,
            channel=channel,
            channel_user_id=channel_user_id,
            display_name=display_name or channel_user_id,
            status="pending",
        )
        self.session.add(member)
        self.session.flush()
        ensure_identity(
            self.session, member, channel=channel, external_id=channel_user_id
        )
        refresh_member_primary_cache(self.session, member)
        return member

    def list_active_members(self) -> list[Member]:
        return list(
            self.session.scalars(
                select(Member).where(Member.team_id == self.team_id, Member.status == "active")
            )
        )

    def add_member(self, channel_user_id: str, display_name: str) -> Member:
        member = self.get_or_create_member(channel_user_id, display_name)
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

    def _ingestions_team_query(self):
        return (
            select(UsageIngestion)
            .outerjoin(Member, UsageIngestion.member_id == Member.id)
            .outerjoin(AiAccount, UsageIngestion.account_id == AiAccount.id)
            .where(
                or_(Member.team_id == self.team_id, AiAccount.team_id == self.team_id)
            )
        )

    def list_ingestions(
        self,
        period: str | None = None,
        status: str | None = None,
    ) -> list[UsageIngestion]:
        query = self._ingestions_team_query()
        if period:
            query = query.where(UsageIngestion.billing_period == period)
        if status:
            query = query.where(UsageIngestion.status == status)
        return list(self.session.scalars(query.order_by(UsageIngestion.ingested_at.desc())))

    def list_pending_ingestions(
        self,
        period: str | None = None,
        *,
        manual_only: bool = False,
    ) -> list[UsageIngestion]:
        query = self._ingestions_team_query().where(
            UsageIngestion.status == "pending_review"
        )
        if manual_only:
            query = query.where(UsageIngestion.source_type != "api_sync")
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

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
