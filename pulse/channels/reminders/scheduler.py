from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pulse.config import AppConfig
from pulse.ingestion.sync_schedule import elevate_pre_publish
from pulse.ingestion.sync_tick import run_sync_tick
from pulse.periods import collection_period_for_config, report_period_for_config
from pulse.storage.models import AiAccountCredential, ReminderLog
from pulse.storage.repository import Repository
from pulse.tool_center.reminders import build_daily_nudge_targets, format_deadline_group_message
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tenant.context import team_repository
from pulse.util.business_days import is_first_business_day

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

    def report_period(self, now: datetime | None = None) -> str:
        now = now or datetime.now(self.tz)
        return report_period_for_config(self.config, now)

    def collection_period(self, now: datetime | None = None) -> str:
        now = now or datetime.now(self.tz)
        return collection_period_for_config(self.config, now)

    def _usage_reminders_enabled(self) -> bool:
        return bool(self.config.collection.reminders_enabled)

    def send_collection_start(self, period: str | None = None) -> None:
        if not self._usage_reminders_enabled():
            logger.info("Usage submission reminders disabled; skip collection_start")
            return
        period = period or self.collection_period()
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
        period = period or self.collection_period()
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
        period = period or self.collection_period()
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

        period = period or self.report_period()
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
            self._log_reminder(None, period, "monthly_report")
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

    def send_pre_publish_refresh_if_needed(self) -> None:
        now = datetime.now(self.tz)
        if not is_first_business_day(now.date()):
            return
        period = self.report_period(now)
        session = self.session_factory()
        try:
            if self._reminder_exists(session, period, "pre_publish_refresh"):
                return
            creds = list(
                session.scalars(
                    select(AiAccountCredential).where(
                        AiAccountCredential.status == "active",
                        AiAccountCredential.sync_enabled.is_(True),
                        AiAccountCredential.key_role == "primary",
                    )
                ).all()
            )
            if creds:
                elevate_pre_publish(creds, now=datetime.now(timezone.utc))
                session.commit()
            self._log_reminder(None, period, "pre_publish_refresh")
            logger.info("Pre-publish cursor sync refresh queued for %s", period)
        finally:
            session.close()

    def send_monthly_report_if_publish_day(self) -> None:
        now = datetime.now(self.tz)
        if not self.config.collection.report_on_first_business_day:
            return
        if not is_first_business_day(now.date()):
            return
        if not self.messenger:
            logger.warning("Monthly report skipped: no messenger")
            return

        period = self.report_period(now)
        session = self.session_factory()
        try:
            if self._reminder_exists(session, period, "monthly_report"):
                logger.info("Monthly report for %s already sent", period)
                return
            if self._reminder_exists(session, period, "monthly_report_blocked"):
                logger.info("Monthly report for %s already blocked today", period)
                return

            from pulse.report.readiness import (
                check_period_readiness,
                format_blocked_report_message,
            )

            team, _repo = team_repository(session, self.config)
            readiness = check_period_readiness(
                session, team.id, period, self.config, now=now
            )
            if not readiness.ready:
                blocked_text = format_blocked_report_message(period, readiness)
                for admin_id in self.config.admin.dingtalk_user_ids:
                    try:
                        self.send_private_message(admin_id, blocked_text)
                    except Exception:
                        logger.exception("Failed to notify admin %s about blocked report", admin_id)
                self._log_reminder(None, period, "monthly_report_blocked")
                logger.warning(
                    "Monthly report blocked for %s: %s issue(s)",
                    period,
                    len(readiness.issues),
                )
                return
        finally:
            session.close()

        self.send_monthly_report(period)

    def run_cursor_sync_tick(self) -> int:
        if not self.config.credentials.encryption_key:
            return 0
        session = self.session_factory()
        try:
            from pulse.settings import effective_config_for_tenant

            runtime_config = effective_config_for_tenant(session, self.config)
            if not runtime_config.cursor_sync.enabled:
                return 0
            return run_sync_tick(
                session,
                runtime_config,
                notify_admins=self.send_private_message,
            )
        finally:
            session.close()

    def run_expire_key_loans(self) -> int:
        encryption_key = self.config.credentials.encryption_key
        if not encryption_key:
            return 0
        session = self.session_factory()
        try:
            from pulse.tool_center.key_loans import KeyLoanService

            svc = KeyLoanService(session, encryption_key)
            expired = svc.expire_loans_on_reset()
            if expired:
                session.commit()
            return expired
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

    def _reminder_exists(self, session: Session, period: str, rtype: str) -> bool:
        row = session.scalar(
            select(ReminderLog.id).where(
                ReminderLog.period == period,
                ReminderLog.type == rtype,
            )
        )
        return row is not None

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
    session = session_factory()
    try:
        from pulse.settings import effective_config_for_tenant

        runtime = effective_config_for_tenant(session, config)
    finally:
        session.close()

    service = ReminderService(
        config, session_factory, send_group_message, send_private_message, messenger=messenger
    )
    scheduler = BackgroundScheduler(timezone=runtime.collection.timezone)
    c = runtime.collection

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

    pre_hour, pre_minute = _split_time(runtime.cursor_sync.pre_publish_start_time)
    tick_minutes = runtime.cursor_sync.tick_interval_minutes

    if c.report_on_first_business_day:
        scheduler.add_job(
            service.send_pre_publish_refresh_if_needed,
            trigger="cron",
            hour=pre_hour,
            minute=pre_minute,
            id="pre_publish_refresh",
        )
        scheduler.add_job(
            service.send_monthly_report_if_publish_day,
            trigger="cron",
            hour=report_hour,
            minute=report_minute,
            id="monthly_report",
        )
    else:
        scheduler.add_job(
            service.send_monthly_report,
            trigger="cron",
            day=c.report_day,
            hour=report_hour,
            minute=report_minute,
            id="monthly_report",
        )

    scheduler.add_job(
        service.run_cursor_sync_tick,
        trigger="interval",
        minutes=max(1, tick_minutes),
        id="cursor_sync_tick",
    )

    scheduler.add_job(
        service.run_expire_key_loans,
        trigger="cron",
        hour=3,
        minute=0,
        id="expire_key_loans",
    )

    # Memory evolution: legacy pulse.memory_adapter removed; Prompt evolution
    # lives in assistant_platform (failure clusters → proposals → canary).

    return scheduler


def _split_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)
