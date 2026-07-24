from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.ingestion.on_demand import (
    OnDemandEnforceResult,
    format_on_demand_admin_alert,
    resolve_admin_dingtalk_ids,
    resolve_on_demand_notify_dingtalk_ids,
)
from pulse.ingestion.sync import CursorSyncService
from pulse.ingestion.sync_errors import FatalSyncError, RetryableSyncError, classify_sync_error
from pulse.ingestion.sync_schedule import (
    account_jitter_sec,
    apply_sync_failure,
    apply_sync_success,
    elevate_month_close_if_needed,
    is_due_for_sync,
)
from pulse.storage.models import AiAccount, AiAccountCredential

logger = logging.getLogger(__name__)

_PRIORITY_ORDER = {"pre_publish": 0, "month_close": 1, "normal": 2}


def _make_on_demand_notify(session: Session, config: AppConfig, send_private_message):
    if not send_private_message or not config.cursor_sync.enforce_on_demand_disabled:
        return None

    def _notify(account: AiAccount, result: OnDemandEnforceResult) -> None:
        text = format_on_demand_admin_alert(account, result)
        if result.status == "check_failed":
            if not config.cursor_sync.on_demand_notify_admins_on_api_failure:
                return
            # GetHardLimit 失败：只通知平台管理员，不发给业务通知名单/主使用人
            recipients = resolve_admin_dingtalk_ids(config)
        else:
            recipients = resolve_on_demand_notify_dingtalk_ids(
                session, config, account
            )
        if not recipients:
            logger.warning(
                "on-demand notify: no recipients for account %s (status=%s)",
                account.id,
                result.status,
            )
            return
        for user_id in recipients:
            try:
                send_private_message(user_id, text)
            except Exception:
                logger.exception(
                    "Failed to notify %s about on-demand enforce", user_id
                )

    return _notify


def run_sync_tick(
    session: Session,
    config: AppConfig,
    *,
    notify_admins=None,
) -> int:
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
    sync = CursorSyncService(
        session,
        encryption_key,
        on_demand_notify=_make_on_demand_notify(session, config, notify_admins),
        enforce_on_demand_disabled=config.cursor_sync.enforce_on_demand_disabled,
    )
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
