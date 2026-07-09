from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pulse.config import AppConfig
from pulse.ingestion.sync import CursorSyncService
from pulse.storage.models import AiAccountCredential, ReminderLog
from pulse.storage.repository import Repository
from pulse.tool_center.reminders import build_daily_nudge_targets, format_deadline_group_message
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tenant.context import team_repository

logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(
        self,
        config: AppConfig,
        session_factory: sessionmaker[Session],
        send_group_message,
        send_private_message,
        messenger=None,
    ):
        self.config = config
        self.session_factory = session_factory
        self.send_group_message = send_group_message
        self.send_private_message = send_private_message
        self.messenger = messenger
        self.tz = ZoneInfo(config.collection.timezone)

    def current_period(self, now: datetime | None = None) -> str:
        now = now or datetime.now(self.tz)
        return now.strftime(self.config.collection.period_format)

    def _usage_reminders_enabled(self) -> bool:
        return bool(self.config.collection.reminders_enabled)

    def send_collection_start(self, period: str | None = None) -> None:
        if not self._usage_reminders_enabled():
            logger.info("Usage submission reminders disabled; skip collection_start")
            return
        period = period or self.current_period()
        c = self.config.collection
        deadline = f"{c.deadline_day} 日 {c.deadline_time}"
        text = (
            f"📊 {period} AI 工具用量收集开始\n\n"
            f"请在 {deadline} 前完成：\n"
            "1. **Cursor 账号**：主使用人私聊绑定 API Key（无需再上传 CSV）\n"
            "2. **其他工具**：打开 Usage / 账单页，导出或截图后私聊提交\n"
            "3. **账号主使用人**负责提交（共享账号只需一人操作）\n"
            "4. 分享本月技巧：私聊发送「心得：…」\n\n"
            "也可在本群 @我 发送（群里不会显示用量细节，结果私聊发你）。"
        )
        self.send_group_message(text, at_all=False)
        self._log_reminder(None, period, "collection_start")

    def send_daily_nudges(self, period: str | None = None) -> int:
        if not self._usage_reminders_enabled():
            logger.info("Usage submission reminders disabled; skip daily_nudges")
            return 0
        period = period or self.current_period()
        if not self._in_collection_window():
            return 0

        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.config)
            tool_repo = ToolCenterRepository(session, team.id)
            active_accounts = tool_repo.list_active_accounts()
            if not active_accounts:
                return self._send_legacy_daily_nudges(session, repo, period)

            targets = build_daily_nudge_targets(tool_repo, period)
            sent = 0
            admin_lines: list[str] = []

            for target in targets:
                if target.kind == "admin_no_primary":
                    admin_lines.append(f"· {target.account.account_identifier}（无主使用人）")
                    continue
                if not target.member:
                    continue
                if self._already_nudged_today(session, target.member.id, period):
                    continue
                if target.kind == "no_credential":
                    text = (
                        f"Hi {target.member.display_name}，{period} 账号 "
                        f"{target.account.account_identifier} 尚未绑定 Cursor API Key。\n\n"
                        "请私聊发送：绑定 cursor key crsr_...（绑定后用量将自动同步，无需上传 CSV）。"
                    )
                elif target.kind == "sync_failed":
                    text = (
                        f"Hi {target.member.display_name}，{period} 账号 "
                        f"{target.account.account_identifier} 的用量同步失败。\n\n"
                        "请检查 API Key 是否仍有效，必要时重新绑定。"
                    )
                else:
                    text = (
                        f"Hi {target.member.display_name}，{period} 账号 "
                        f"{target.account.account_identifier} 的用量还未收到。\n\n"
                        "你是该账号主使用人，请导出用量后私聊发给我。"
                    )
                self.send_private_message(target.member.dingtalk_user_id, text)
                self._log_reminder(target.member.id, period, "daily_dm")
                sent += 1

            stale_lines = self._cursor_sync_stale_lines(session, tool_repo)
            if stale_lines and not self._already_nudged_today(session, None, period, "sync_stale"):
                stale_text = (
                    f"【管理员待办】{period} 以下 Cursor 账号同步滞后超过 36 小时：\n"
                    + "\n".join(stale_lines)
                )
                for admin_id in self.config.admin.dingtalk_user_ids:
                    self.send_private_message(admin_id, stale_text)
                self._log_reminder(None, period, "sync_stale")
                sent += 1

            if admin_lines and not self._already_nudged_today(session, None, period, "admin_no_primary"):
                admin_text = (
                    f"【管理员待办】{period} 以下账号未指定主使用人，无法催办提交：\n"
                    + "\n".join(admin_lines)
                )
                for admin_id in self.config.admin.dingtalk_user_ids:
                    self.send_private_message(admin_id, admin_text)
                self._log_reminder(None, period, "admin_no_primary")
                sent += 1

            session.commit()
            return sent
        finally:
            session.close()

    def _send_legacy_daily_nudges(self, session: Session, repo: Repository, period: str) -> int:
        unsubmitted = repo.get_unsubmitted_members(period)
        sent = 0
        for member in unsubmitted:
            if self._already_nudged_today(session, member.id, period):
                continue
            text = (
                f"Hi {member.display_name}，{period} Cursor 用量还未收到。\n\n"
                "导出步骤：Dashboard → Usage → 选日期 → Export CSV\n"
                "直接发给我就行。"
            )
            self.send_private_message(member.dingtalk_user_id, text)
            self._log_reminder(member.id, period, "daily_dm")
            sent += 1
        session.commit()
        return sent

    def send_deadline_reminder(self, period: str | None = None) -> None:
        if not self._usage_reminders_enabled():
            logger.info("Usage submission reminders disabled; skip deadline_reminder")
            return
        period = period or self.current_period()
        session = self.session_factory()
        try:
            team, repo = team_repository(session, self.config)
            tool_repo = ToolCenterRepository(session, team.id)
            active_accounts = tool_repo.list_active_accounts()
            if active_accounts:
                submitted = tool_repo.get_submitted_account_ids(period)
                missing_primary = len(tool_repo.accounts_missing_primary())
                text = format_deadline_group_message(
                    period=period,
                    total_accounts=len(active_accounts),
                    submitted_count=len(submitted),
                    missing_primary_count=missing_primary,
                )
            else:
                active = repo.list_active_members()
                submitted_ids = repo.get_submitted_member_ids(period)
                unsubmitted = [m for m in active if m.id not in submitted_ids]
                names = "、".join(m.display_name for m in unsubmitted) or "无"
                text = (
                    f"⏰ {period} 用量提交截止提醒\n\n"
                    f"已提交：{len(submitted_ids)}/{len(active)} 人\n"
                    f"尚未提交：{names}\n\n"
                    "请尚未提交的同学尽快私聊我发送 CSV。"
                )
            self.send_group_message(text, at_all=True)
            self._log_reminder(None, period, "deadline_at_all")
            session.commit()
        finally:
            session.close()

    def send_monthly_report(self, period: str | None = None) -> None:
        if not self.messenger:
            logger.warning("Monthly report skipped: no messenger")
            return
        from pulse.alerts.service import run_anomaly_check
        from pulse.report.service import publish_report_to_group

        period = period or self.current_period()
        session = self.session_factory()
        try:
            team, _repo = team_repository(session, self.config)
            publish_report_to_group(
                session,
                period,
                self.messenger,
                team_id=team.id,
                config=self.config,
            )
            run_anomaly_check(
                session,
                self.config,
                team.id,
                period,
                notify_admins=self.send_private_message,
            )
            self._send_v2_briefings(session, team.id, period)
            session.commit()
            logger.info("Published monthly report and alerts for %s", period)
        except Exception:
            logger.exception("Failed to publish monthly report")
            session.rollback()
        finally:
            session.close()

    def _send_v2_briefings(self, session: Session, team_id: str, period: str) -> None:
        from pulse.tool_center.aggregate import aggregate_account_metrics
        from pulse.tool_center.briefing import build_anonymous_group_digest, build_manager_briefing

        metrics = aggregate_account_metrics(session, period, team_id=team_id)
        if not metrics.get("account_count_active"):
            return

        briefing = build_manager_briefing(session, period, team_id=team_id)
        for admin_id in self.config.admin.dingtalk_user_ids:
            try:
                self.send_private_message(admin_id, briefing)
            except Exception:
                logger.exception("Failed to send manager briefing to %s", admin_id)

        digest = build_anonymous_group_digest(session, period, team_id=team_id)
        try:
            self.send_group_message(digest, at_all=False)
        except Exception:
            logger.exception("Failed to send anonymous group digest")

        from pulse.tool_center.knowledge import KnowledgeService

        tip_digest = KnowledgeService(session, team_id, self.config).build_monthly_digest(period)
        if tip_digest:
            try:
                self.send_group_message(tip_digest, at_all=False)
            except Exception:
                logger.exception("Failed to send knowledge digest")

    def run_daily_cursor_sync(self) -> int:
        encryption_key = self.config.credentials.encryption_key
        if not encryption_key:
            logger.warning("PULSE_CREDENTIAL_ENCRYPTION_KEY not set; skip daily cursor sync")
            return 0

        session = self.session_factory()
        synced = 0
        try:
            creds = list(
                session.scalars(
                    select(AiAccountCredential).where(
                        AiAccountCredential.status == "active",
                        AiAccountCredential.sync_enabled.is_(True),
                    )
                ).all()
            )
            if not creds:
                return 0

            sync = CursorSyncService(session, encryption_key)
            for cred in creds:
                try:
                    sync.sync_account(cred.account_id, channel="scheduler")
                    synced += 1
                except Exception:
                    logger.exception("cursor sync failed for %s", cred.account_id)
            return synced
        finally:
            session.close()

    def _cursor_sync_stale_lines(self, session: Session, tool_repo: ToolCenterRepository) -> list[str]:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(hours=36)
        lines: list[str] = []
        for account in tool_repo.list_active_accounts():
            if not account.vendor or account.vendor.slug != "cursor":
                continue
            cred = session.scalar(
                select(AiAccountCredential).where(AiAccountCredential.account_id == account.id)
            )
            if not cred or cred.status != "active":
                continue
            if cred.last_sync_status == "failed":
                continue
            if cred.last_sync_at and cred.last_sync_at < stale_before:
                lines.append(f"· {account.account_identifier}")
        return lines

    def _in_collection_window(self) -> bool:
        now = datetime.now(self.tz)
        c = self.config.collection
        if now.day < c.start_day:
            return False
        if now.day > c.deadline_day:
            return False
        return True

    def _already_nudged_today(
        self,
        session: Session,
        member_id: str | None,
        period: str,
        rtype: str = "daily_dm",
    ) -> bool:
        from sqlalchemy import select

        today = datetime.now(self.tz).date()
        query = select(ReminderLog).where(
            ReminderLog.period == period,
            ReminderLog.type == rtype,
        )
        if member_id is None:
            query = query.where(ReminderLog.member_id.is_(None))
        else:
            query = query.where(ReminderLog.member_id == member_id)
        rows = session.scalars(query).all()
        for row in rows:
            if row.sent_at.astimezone(self.tz).date() == today:
                return True
        return False

    def _log_reminder(self, member_id: str | None, period: str, rtype: str) -> None:
        session = self.session_factory()
        try:
            session.add(ReminderLog(member_id=member_id, period=period, type=rtype))
            session.commit()
        finally:
            session.close()


def build_scheduler(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    send_group_message,
    send_private_message,
    messenger=None,
) -> BackgroundScheduler:
    service = ReminderService(
        config, session_factory, send_group_message, send_private_message, messenger=messenger
    )
    scheduler = BackgroundScheduler(timezone=config.collection.timezone)
    c = config.collection

    start_hour, start_minute = _split_time(c.start_time)
    daily_hour, daily_minute = _split_time(c.daily_check_time)
    deadline_hour, deadline_minute = _split_time(c.deadline_time)
    report_hour, report_minute = _split_time(c.report_time)

    if c.reminders_enabled:
        scheduler.add_job(
            service.send_collection_start,
            trigger="cron",
            day=c.start_day,
            hour=start_hour,
            minute=start_minute,
            id="collection_start",
        )
        scheduler.add_job(
            service.send_daily_nudges,
            trigger="cron",
            hour=daily_hour,
            minute=daily_minute,
            id="daily_nudge",
        )
        scheduler.add_job(
            service.send_deadline_reminder,
            trigger="cron",
            day=c.deadline_day,
            hour=deadline_hour,
            minute=deadline_minute,
            id="deadline_reminder",
        )
    else:
        logger.info("Usage submission reminders disabled; collection_start/daily_nudge/deadline not scheduled")

    scheduler.add_job(
        service.send_monthly_report,
        trigger="cron",
        day=c.report_day,
        hour=report_hour,
        minute=report_minute,
        id="monthly_report",
    )

    scheduler.add_job(
        service.run_daily_cursor_sync,
        trigger="cron",
        hour=2,
        minute=0,
        id="daily_cursor_sync",
    )

    if config.memory.evolution_enabled:
        evo_hour, evo_minute = _split_time(config.memory.evolution_time)
        from pulse.memory_adapter.evolution_job import run_memory_evolution

        scheduler.add_job(
            lambda: run_memory_evolution(
                session_factory,
                config,
                send_private_message=send_private_message,
                send_group_message=send_group_message,
            ),
            trigger="cron",
            day_of_week=config.memory.evolution_day_of_week,
            hour=evo_hour,
            minute=evo_minute,
            id="memory_evolution",
        )

    return scheduler


def _split_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)
