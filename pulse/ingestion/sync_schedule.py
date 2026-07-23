from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from pulse.config import AppConfig, CursorSyncConfig
from pulse.ingestion.sync_errors import FatalSyncError, RetryableSyncError
from pulse.periods import report_period_for_config
from pulse.storage.models import AiAccountCredential
from sqlalchemy.orm import Session


def account_jitter_sec(account_id: str, *, max_sec: int = 3600) -> int:
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return int(digest[:8], 16) % max(1, max_sec)


def _utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def backoff_seconds(retry_count: int, *, cap: int = 3600) -> int:
    base = min(cap, 60 * (2 ** min(retry_count, 6)))
    jitter = random.uniform(0, base * 0.3)
    return int(base + jitter)


def init_schedule_on_bind(cred: AiAccountCredential, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    cred.sync_jitter_sec = account_jitter_sec(cred.account_id)
    cred.next_sync_at = now + timedelta(seconds=cred.sync_jitter_sec)
    cred.next_retry_at = None
    cred.sync_priority = "normal"
    cred.retry_count = 0


def apply_sync_success(
    cred: AiAccountCredential,
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    cred.retry_count = 0
    cred.next_retry_at = None
    if cred.sync_priority == "pre_publish":
        cred.sync_priority = "month_close"
    interval_minutes = _effective_interval_minutes(cred, config, now)
    cred.next_sync_at = now + timedelta(minutes=interval_minutes, seconds=cred.sync_jitter_sec)


def apply_sync_failure(
    cred: AiAccountCredential,
    exc: BaseException,
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    classified = exc if isinstance(exc, (RetryableSyncError, FatalSyncError)) else None
    if classified is None:
        from pulse.ingestion.sync_errors import classify_sync_error

        classified = classify_sync_error(exc)

    if isinstance(classified, FatalSyncError):
        cred.retry_count = 0
        cred.next_retry_at = None
        return

    sync_cfg = config.cursor_sync
    cred.retry_count = (cred.retry_count or 0) + 1
    if cred.retry_count >= sync_cfg.max_retry_count:
        cred.next_retry_at = None
        return
    cred.next_retry_at = now + timedelta(seconds=backoff_seconds(cred.retry_count))


def elevate_pre_publish(creds: list[AiAccountCredential], *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    for cred in creds:
        cred.sync_priority = "pre_publish"
        cred.next_sync_at = now


def elevate_month_close_if_needed(
    cred: AiAccountCredential,
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    if cred.sync_priority == "pre_publish":
        return
    if not _in_month_close_window(config, now):
        if cred.sync_priority == "month_close":
            cred.sync_priority = "normal"
        return
    if cred.sync_priority == "normal":
        cred.sync_priority = "month_close"


def _effective_interval_minutes(
    cred: AiAccountCredential,
    config: AppConfig,
    now: datetime,
) -> int:
    sync_cfg = config.cursor_sync
    if cred.sync_priority == "pre_publish":
        return 0
    if _in_month_close_window(config, now) or cred.sync_priority == "month_close":
        return sync_cfg.month_close_interval_minutes
    return sync_cfg.default_interval_minutes


def _in_month_close_window(config: AppConfig, now: datetime) -> bool:
    local = now.astimezone(_tz(config))
    report_period = report_period_for_config(config, local)
    current = local.strftime(config.collection.period_format)
    if report_period == current:
        return False
    first_bd = _first_business_day(local.year, local.month)
    publish_at = datetime(
        first_bd.year,
        first_bd.month,
        first_bd.day,
        *_split_hm(config.collection.report_time),
        tzinfo=local.tzinfo,
    )
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start <= local < publish_at


def _first_business_day(year: int, month: int):
    from pulse.util.business_days import first_business_day_of_month

    return first_business_day_of_month(year, month)


def _tz(config: AppConfig):
    from zoneinfo import ZoneInfo

    return ZoneInfo(config.collection.timezone)


def _split_hm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def is_due_for_sync(cred: AiAccountCredential, now: datetime) -> bool:
    if cred.next_retry_at and _utc_aware(cred.next_retry_at) <= now and (cred.retry_count or 0) > 0:
        return True
    if cred.next_sync_at is None:
        return True
    return _utc_aware(cred.next_sync_at) <= now


def accelerate_sync_schedules(session: Session, config: AppConfig, *, now: datetime | None = None) -> int:
    """Pull forward next_sync_at after admin shortens sync interval."""
    now = now or datetime.now(timezone.utc)
    creds = session.scalars(
        select(AiAccountCredential).where(
            AiAccountCredential.status == "active",
            AiAccountCredential.sync_enabled.is_(True),
            AiAccountCredential.key_role == "primary",
        )
    ).all()
    changed = 0
    for cred in creds:
        elevate_month_close_if_needed(cred, config, now=now)
        interval = _effective_interval_minutes(cred, config, now)
        if interval <= 0:
            next_at = cred.next_sync_at
            if next_at is not None:
                next_at = _utc_aware(next_at)
            if next_at is None or next_at > now:
                cred.next_sync_at = now
                changed += 1
            continue
        cap = now + timedelta(minutes=interval)
        next_at = cred.next_sync_at
        if next_at is not None:
            next_at = _utc_aware(next_at)
        if next_at is None or next_at > cap:
            cred.next_sync_at = now
            changed += 1
    if changed:
        session.flush()
    return changed
