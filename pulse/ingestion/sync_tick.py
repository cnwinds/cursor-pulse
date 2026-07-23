from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.ingestion.sync import CursorSyncService
from pulse.ingestion.sync_errors import FatalSyncError, RetryableSyncError, classify_sync_error
from pulse.ingestion.sync_schedule import (
    account_jitter_sec,
    apply_sync_failure,
    apply_sync_success,
    elevate_month_close_if_needed,
    is_due_for_sync,
)
from pulse.storage.models import AiAccountCredential

logger = logging.getLogger(__name__)

_PRIORITY_ORDER = {"pre_publish": 0, "month_close": 1, "normal": 2}


def run_sync_tick(session: Session, config: AppConfig) -> int:
    encryption_key = config.credentials.encryption_key
    if not encryption_key or not config.cursor_sync.enabled:
        return 0

    now = datetime.now(timezone.utc)
    creds = list(
        session.scalars(
            select(AiAccountCredential).where(
                AiAccountCredential.status == "active",
                AiAccountCredential.sync_enabled.is_(True),
                AiAccountCredential.key_role == "primary",
            )
        ).all()
    )
    if not creds:
        return 0

    for cred in creds:
        if not cred.sync_jitter_sec:
            cred.sync_jitter_sec = account_jitter_sec(cred.account_id)
        elevate_month_close_if_needed(cred, config, now=now)

    due = [c for c in creds if is_due_for_sync(c, now)]
    due.sort(
        key=lambda c: (
            _PRIORITY_ORDER.get(c.sync_priority or "normal", 9),
            c.next_retry_at or c.next_sync_at or now,
        )
    )

    batch_size = config.cursor_sync.batch_size
    if any(c.sync_priority == "pre_publish" for c in due):
        batch_size = max(batch_size, config.cursor_sync.pre_publish_batch_size)

    synced = 0
    sync = CursorSyncService(session, encryption_key)
    for cred in due[:batch_size]:
        try:
            sync.sync_account(cred.account_id, channel="scheduler")
            cred = session.get(AiAccountCredential, cred.id) or cred
            apply_sync_success(cred, config, now=datetime.now(timezone.utc))
            synced += 1
        except Exception as exc:
            session.rollback()
            cred = session.get(AiAccountCredential, cred.id)
            if not cred:
                logger.exception("credential missing after sync failure")
                continue
            classified = classify_sync_error(exc)
            cred.last_sync_status = "failed"
            cred.last_sync_error = str(classified)
            apply_sync_failure(cred, classified, config, now=datetime.now(timezone.utc))
            session.commit()
            if isinstance(classified, FatalSyncError):
                logger.warning(
                    "cursor sync fatal for account %s: %s", cred.account_id, classified
                )
            else:
                logger.warning(
                    "cursor sync retryable for account %s (retry=%s): %s",
                    cred.account_id,
                    cred.retry_count,
                    classified,
                )
        else:
            session.commit()
    return synced
