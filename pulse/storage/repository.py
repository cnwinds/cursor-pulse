from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pulse.domain import ParsedCsv, UsageEventRecord
from pulse.storage.models import Member, Submission, UsageRecord


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
            select(Submission.member_id)
            .join(Member)
            .where(
                Member.team_id == self.team_id,
                Submission.billing_period == period,
                Submission.status == "confirmed",
            )
        )
        return set(rows)

    def get_unsubmitted_members(self, period: str) -> list[Member]:
        submitted = self.get_submitted_member_ids(period)
        return [m for m in self.list_active_members() if m.id not in submitted]

    def _delete_confirmed_period_records(self, member_id: str, period: str) -> None:
        old_submissions = self.session.scalars(
            select(Submission).where(
                Submission.member_id == member_id,
                Submission.billing_period == period,
                Submission.status == "confirmed",
            )
        ).all()
        for sub in old_submissions:
            self.session.execute(delete(UsageRecord).where(UsageRecord.submission_id == sub.id))
            self.session.delete(sub)

    def list_pending_submissions(self, period: str | None = None) -> list[Submission]:
        query = (
            select(Submission)
            .join(Member)
            .where(Member.team_id == self.team_id, Submission.status == "pending_review")
        )
        if period:
            query = query.where(Submission.billing_period == period)
        return list(self.session.scalars(query.order_by(Submission.submitted_at.desc())))

    def find_submission_by_id_prefix(self, prefix: str) -> Submission | None:
        rows = list(
            self.session.scalars(
                select(Submission)
                .join(Member)
                .where(
                    Member.team_id == self.team_id,
                    Submission.id.like(f"{prefix}%"),
                )
            )
        )
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise ValueError(f"提交 ID 前缀 {prefix!r} 不唯一，请提供更多字符")
        return None

    def confirm_submission(self, submission_id: str) -> Submission:
        sub = self.session.get(Submission, submission_id)
        if not sub or sub.status != "pending_review":
            raise ValueError("未找到待审提交或状态不正确")
        member = self.session.get(Member, sub.member_id)
        if not member or member.team_id != self.team_id:
            raise ValueError("无权操作该提交")
        self._delete_confirmed_period_records(member.id, sub.billing_period)
        sub.status = "confirmed"
        sub.confirmed_at = datetime.now(timezone.utc)
        self.session.flush()
        return sub

    def reject_submission(self, submission_id: str) -> None:
        sub = self.session.get(Submission, submission_id)
        if not sub or sub.status != "pending_review":
            raise ValueError("未找到待审提交或状态不正确")
        member = self.session.get(Member, sub.member_id)
        if not member or member.team_id != self.team_id:
            raise ValueError("无权操作该提交")
        self.session.execute(delete(UsageRecord).where(UsageRecord.submission_id == sub.id))
        self.session.delete(sub)
        self.session.flush()

    def save_csv_submission(
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
    ) -> Submission:
        return self.save_submission(
            member=member,
            period=period,
            parsed=parsed,
            submit_channel=submit_channel,
            input_type=input_type,
            raw_source=raw_source,
            raw_files_dir=raw_files_dir,
            raw_text=raw_text,
        )

    def save_submission(
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
    ) -> Submission:
        if status == "confirmed":
            self._delete_confirmed_period_records(member.id, period)

        raw_file_path: str | None = None
        if raw_source and raw_files_dir:
            raw_files_dir.mkdir(parents=True, exist_ok=True)
            dest = raw_files_dir / f"{member.id}_{period}_{raw_source.name}"
            shutil.copy2(raw_source, dest)
            raw_file_path = str(dest)
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

        now = datetime.now(timezone.utc)
        submission = Submission(
            member_id=member.id,
            billing_period=period,
            input_type=input_type,
            submit_channel=submit_channel,
            raw_file_path=raw_file_path,
            raw_text=raw_text,
            status=status,
            submitted_at=now,
            confirmed_at=now if status == "confirmed" else None,
        )
        self.session.add(submission)
        self.session.flush()

        for rec in parsed.records:
            self.session.add(
                self._to_usage_record(rec, submission.id, member.id, extraction_confidence)
            )

        self.session.flush()
        return submission

    @staticmethod
    def _to_usage_record(
        rec: UsageEventRecord,
        submission_id: str,
        member_id: str,
        extraction_confidence: float = 1.0,
    ) -> UsageRecord:
        return UsageRecord(
            submission_id=submission_id,
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
            cost_usd=float(rec.cost_usd),
            cloud_agent_id=rec.cloud_agent_id,
            automation_id=rec.automation_id,
            source_row_hash=rec.source_row_hash,
            extraction_confidence=extraction_confidence,
        )

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
