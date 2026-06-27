from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from pulse.config import AppConfig
from pulse.storage.models import ReminderLog
from pulse.storage.repository import Repository
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

    def send_collection_start(self, period: str | None = None) -> None:
        period = period or self.current_period()
        c = self.config.collection
        deadline = f"{c.deadline_day} 日 {c.deadline_time}"
        text = (
            f"📊 {period} Cursor 用量收集开始\n\n"
            f"请在 {deadline} 前完成提交：\n"
            "1. 打开 cursor.com/dashboard → Usage\n"
            "2. 选择账期日期范围 → Export CSV\n"
            "3. **私聊本机器人**发送 CSV 文件（推荐，数据不公开）\n\n"
            "也可在本群 @我 发送（群里不会显示你的用量细节，结果私聊发你）。"
        )
        self.send_group_message(text, at_all=False)
        self._log_reminder(None, period, "collection_start")

    def send_daily_nudges(self, period: str | None = None) -> int:
        period = period or self.current_period()
        if not self._in_collection_window():
            return 0

        session = self.session_factory()
        try:
            _team, repo = team_repository(session, self.config)
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
        finally:
            session.close()

    def send_deadline_reminder(self, period: str | None = None) -> None:
        period = period or self.current_period()
        session = self.session_factory()
        try:
            _team, repo = team_repository(session, self.config)
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
            session.commit()
            logger.info("Published monthly report and alerts for %s", period)
        except Exception:
            logger.exception("Failed to publish monthly report")
            session.rollback()
        finally:
            session.close()

    def _in_collection_window(self) -> bool:
        now = datetime.now(self.tz)
        c = self.config.collection
        if now.day < c.start_day:
            return False
        if now.day > c.deadline_day:
            return False
        return True

    def _already_nudged_today(self, session: Session, member_id: str, period: str) -> bool:
        from sqlalchemy import select

        today = datetime.now(self.tz).date()
        rows = session.scalars(
            select(ReminderLog).where(
                ReminderLog.member_id == member_id,
                ReminderLog.period == period,
                ReminderLog.type == "daily_dm",
            )
        ).all()
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
    scheduler.add_job(
        service.send_monthly_report,
        trigger="cron",
        day=c.report_day,
        hour=report_hour,
        minute=report_minute,
        id="monthly_report",
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
