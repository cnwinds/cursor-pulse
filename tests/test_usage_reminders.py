from unittest.mock import MagicMock

from pulse.channels.reminders.scheduler import SyncSchedulerService, build_scheduler
from pulse.config import AppConfig, CollectionConfig, CredentialConfig, CursorSyncConfig


def test_build_scheduler_only_sync_and_loan_jobs():
    config = AppConfig(
        collection=CollectionConfig(),
        credentials=CredentialConfig(encryption_key="test-key"),
        cursor_sync=CursorSyncConfig(enabled=True, tick_interval_minutes=2),
    )
    scheduler = build_scheduler(config, MagicMock(), MagicMock(), MagicMock())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"cursor_sync_tick", "expire_key_loans"}


def test_sync_scheduler_service_runs_without_encryption_key():
    config = AppConfig(credentials=CredentialConfig(encryption_key=""))
    service = SyncSchedulerService(config, MagicMock())
    assert service.run_cursor_sync_tick() == 0
    assert service.run_expire_key_loans() == 0
