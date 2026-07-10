from unittest.mock import MagicMock

from pulse.bot.reminders.scheduler import ReminderService, build_scheduler
from pulse.config import AppConfig, CollectionConfig


def test_usage_reminders_disabled_by_default():
    assert CollectionConfig().reminders_enabled is False


def test_send_daily_nudges_skipped_when_disabled():
    config = AppConfig(collection=CollectionConfig(reminders_enabled=False))
    send_group = MagicMock()
    send_private = MagicMock()
    service = ReminderService(config, MagicMock(), send_group, send_private)
    assert service.send_daily_nudges("2026-06") == 0
    send_private.assert_not_called()
    send_group.assert_not_called()


def test_build_scheduler_omits_usage_reminder_jobs_when_disabled():
    config = AppConfig(collection=CollectionConfig(reminders_enabled=False))
    scheduler = build_scheduler(config, MagicMock(), MagicMock(), MagicMock())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "collection_start" not in job_ids
    assert "daily_nudge" not in job_ids
    assert "deadline_reminder" not in job_ids
    assert "monthly_report" in job_ids
