from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.periods import period_end_datetime
from pulse.storage.models import AiAccount, AiAccountCredential, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository


@dataclass
class AccountReadinessIssue:
    account_id: str
    account_identifier: str
    reason: str


@dataclass
class ReadinessResult:
    ready: bool
    issues: list[AccountReadinessIssue] = field(default_factory=list)
    data_as_of: datetime | None = None


def check_period_readiness(
    session: Session,
    team_id: str,
    period: str,
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> ReadinessResult:
    tz = ZoneInfo(config.collection.timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    tool_repo = ToolCenterRepository(session, team_id)
    submitted = tool_repo.get_submitted_account_ids(period)
    max_age = timedelta(hours=config.cursor_sync.readiness_sync_max_age_hours)
    period_end = period_end_datetime(period, config.collection.timezone)

    issues: list[AccountReadinessIssue] = []
    latest_sync: datetime | None = None

    for account in tool_repo.list_active_accounts():
        if not account.primary_member_id:
            issues.append(
                AccountReadinessIssue(
                    account.id,
                    account.account_identifier,
                    "未指定主使用人",
                )
            )
            continue

        cred = session.scalar(
            select(AiAccountCredential).where(
                AiAccountCredential.account_id == account.id,
                AiAccountCredential.key_role == "primary",
                AiAccountCredential.status == "active",
            )
        )
        is_cursor_api = (
            account.vendor
            and account.vendor.slug == "cursor"
            and cred
            and cred.sync_enabled
        )

        reason: str | None = None

        if is_cursor_api:
            if cred.last_sync_status != "success":
                reason = f"Cursor 同步未成功（{cred.last_sync_status}）"
            elif not cred.last_sync_at:
                reason = "Cursor 从未成功同步"
            else:
                sync_at = cred.last_sync_at
                if sync_at.tzinfo is None:
                    sync_at = sync_at.replace(tzinfo=timezone.utc)
                sync_local = sync_at.astimezone(tz)
                if latest_sync is None or sync_local > latest_sync:
                    latest_sync = sync_local
                fresh_enough = sync_local >= now - max_age
                covers_period = sync_local > period_end
                if not (fresh_enough or covers_period):
                    reason = "Cursor 同步数据过旧，未覆盖账期末尾"

        if reason is None and account.id not in submitted:
            has_summary = session.scalar(
                select(UsageSummary.id).where(
                    UsageSummary.account_id == account.id,
                    UsageSummary.period == period,
                )
            )
            if not has_summary:
                reason = (
                    "账期无已确认用量数据"
                    if is_cursor_api
                    else "尚未提交账期用量"
                )

        if reason:
            issues.append(
                AccountReadinessIssue(account.id, account.account_identifier, reason)
            )

    return ReadinessResult(
        ready=len(issues) == 0,
        issues=issues,
        data_as_of=latest_sync,
    )


def format_blocked_report_message(period: str, result: ReadinessResult) -> str:
    lines = [
        f"【月报未发布】{period} 以下账号数据未就绪，已阻塞群发：",
        "",
    ]
    for issue in result.issues:
        lines.append(f"· {issue.account_identifier}：{issue.reason}")
    lines.extend(
        [
            "",
            "请补齐数据后手动执行：报告 "
            + period,
        ]
    )
    return "\n".join(lines)
