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
            expired = svc.expire_loans_on_reset()
            if expired:
                session.commit()
            return expired
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

    return scheduler
