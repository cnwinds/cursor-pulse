from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from assistant_platform.storage.models import BackgroundJobRow

_SESSION_LOCK_JOB_TYPES = frozenset({"session.process", "session.close"})

# Lower rank = higher priority when scanning the pending window.
_JOB_TYPE_PRIORITY: dict[str, int] = {
    "reply.send": 0,
    "session.process": 1,
    "noop.phase0": 2,
    "session.close": 10,
}

INTERACTIVE_JOB_TYPES = frozenset({"session.process", "reply.send", "noop.phase0"})
BACKGROUND_JOB_TYPES = frozenset({"session.close"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _begin_claim_transaction(db_session: Session) -> None:
    """SQLite needs IMMEDIATE to serialize concurrent claim attempts."""
    if db_session.get_bind().dialect.name == "sqlite":
        db_session.execute(text("BEGIN IMMEDIATE"))


def _job_sort_key(job: BackgroundJobRow) -> tuple:
    priority = _JOB_TYPE_PRIORITY.get(job.job_type, 5)
    created = job.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return (priority, created, job.id)


def claim_next_job(
    db_session: Session,
    *,
    blocked_session_ids: set[str],
    allowed_job_types: set[str] | frozenset[str] | None = None,
) -> BackgroundJobRow | None:
    """Claim the highest-priority pending job.

    Interactive turns (``session.process`` / ``reply.send``) outrank background
    work such as ``session.close`` (archive / distill), so summarization cannot
    starve live chats when they share a worker.

    ``allowed_job_types`` further restricts which job types this worker may take
    (used to put close/distill on a dedicated background pool).
    """
    _begin_claim_transaction(db_session)
    try:
        if allowed_job_types is not None and not allowed_job_types:
            db_session.rollback()
            return None
        stmt = select(BackgroundJobRow).where(BackgroundJobRow.status == "pending")
        # Filter in SQL so a large backlog of disallowed types (e.g. session.close)
        # cannot fill the claim window and starve interactive workers.
        if allowed_job_types is not None:
            stmt = stmt.where(BackgroundJobRow.job_type.in_(tuple(allowed_job_types)))
        jobs = list(
            db_session.scalars(stmt.order_by(BackgroundJobRow.created_at.asc()).limit(100)).all()
        )
        jobs.sort(key=_job_sort_key)
        for job in jobs:
            session_id = str(job.payload_json.get("session_id") or "")
            if (
                job.job_type in _SESSION_LOCK_JOB_TYPES
                and session_id
                and session_id in blocked_session_ids
            ):
                continue
            updated = db_session.execute(
                update(BackgroundJobRow)
                .where(
                    BackgroundJobRow.id == job.id,
                    BackgroundJobRow.status == "pending",
                )
                .values(status="processing", updated_at=_utcnow())
            )
            if updated.rowcount != 1:
                continue
            db_session.commit()
            db_session.refresh(job)
            return job
        db_session.rollback()
        return None
    except Exception:
        db_session.rollback()
        raise
