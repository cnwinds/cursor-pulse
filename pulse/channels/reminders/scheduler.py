from __future__ import annotations

import logging
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from pulse.config import AppConfig

logger = logging.getLogger(__name__)


class SyncSchedulerService:
    """Channel background jobs: Cursor API sync and key-loan expiry."""

    def __init__(
        self,
        config: AppConfig,
        session_factory: sessionmaker[Session],
        *,
        send_private_message: Callable[[str, str], object] | None = None,
    ):
        self.config = config
        self.session_factory = session_factory
        self.send_private_message = send_private_message

    def run_cursor_sync_tick(self) -> int:
        if not self.config.credentials.encryption_key:
            return 0
        session = self.session_factory()
        try:
            from pulse.settings import effective_config_for_tenant

            runtime_config = effective_config_for_tenant(session, self.config)
            if not runtime_config.cursor_sync.enabled:
                return 0
            from pulse.ingestion.sync_tick import run_sync_tick

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
            expired = svc.expire_loans_on_reset(notify_config=self.config)
            if expired:
                session.commit()
                svc.flush_expire_notifications()
            return expired
        finally:
            session.close()

    def run_auto_lender_reevaluate(self) -> int:
        """重评 auto 模式的借用并换绑；返回换绑笔数。

        只在 ``loan_selection.auto_mode`` 打开时干活，其余情况直接返回 0。
        """
        encryption_key = self.config.credentials.encryption_key
        if not encryption_key:
            return 0
        session = self.session_factory()
        try:
            from pulse.settings import effective_config_for_tenant

            runtime_config = effective_config_for_tenant(session, self.config)
            loan_selection = runtime_config.tool_center.loan_selection
            if not loan_selection.auto_mode:
                return 0

            from pulse.llm.jev import build_jev_client
            from pulse.tenant.service import resolve_team
            from pulse.tool_center.key_loan_auto import (
                record_auto_lender_decision,
                reevaluate_auto_loans,
            )

            team = resolve_team(session, runtime_config)
            stats = reevaluate_auto_loans(
                session,
                encryption_key,
                team_id=team.id,
                loan_selection=loan_selection,
                jev=build_jev_client(runtime_config),
                jev_config=runtime_config.jev,
                on_decision=lambda result: record_auto_lender_decision(session, result),
            )
            return stats["switched"]
        except Exception:
            logger.exception("auto lender re-evaluate failed")
            session.rollback()
            return 0
        finally:
            session.close()


# Backward-compatible alias for imports in tests / legacy code.
ReminderService = SyncSchedulerService


def build_scheduler(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    send_group_message=None,
    send_private_message=None,
    messenger=None,
) -> BackgroundScheduler:
    del send_group_message, messenger  # reserved for future outbound jobs
    session = session_factory()
    try:
        from pulse.settings import effective_config_for_tenant

        runtime = effective_config_for_tenant(session, config)
    finally:
        session.close()

    service = SyncSchedulerService(
        config,
        session_factory,
        send_private_message=send_private_message,
    )
    scheduler = BackgroundScheduler(timezone=runtime.collection.timezone)
    tick_minutes = runtime.cursor_sync.tick_interval_minutes

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

    scheduler.add_job(
        service.run_auto_lender_reevaluate,
        trigger="interval",
        minutes=max(1, tick_minutes),
        id="auto_lender_reevaluate",
    )

    return scheduler
