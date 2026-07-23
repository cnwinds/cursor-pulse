from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow
from assistant_platform.memory.archive_models import SessionArchiveRow

DEFAULT_RETENTION_DAYS = 180


def purge_messages_older_than(
    session: Session,
    *,
    days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> int:
    """Delete aged ledger messages only when their session archive stage is ready.

    Permanent ``ap_archive_*`` rows are never touched by this purge.
    """
    effective_now = now or datetime.now(timezone.utc)
    cutoff = effective_now - timedelta(days=days)
    archived_session_ids = select(SessionArchiveRow.session_id).where(
        SessionArchiveRow.archive_status == "ready"
    )
    stmt = delete(ChatMessageRow).where(
        ChatMessageRow.created_at < cutoff,
        ChatMessageRow.session_id.in_(archived_session_ids),
    )
    result = session.execute(stmt)
    return int(result.rowcount or 0)


def count_expired_messages(
    session: Session,
    *,
    days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> int:
    effective_now = now or datetime.now(timezone.utc)
    cutoff = effective_now - timedelta(days=days)
    archived_session_ids = select(SessionArchiveRow.session_id).where(
        SessionArchiveRow.archive_status == "ready"
    )
    return int(
        session.scalar(
            select(func.count())
            .select_from(ChatMessageRow)
            .where(
                ChatMessageRow.created_at < cutoff,
                ChatMessageRow.session_id.in_(archived_session_ids),
            )
        )
        or 0
    )
