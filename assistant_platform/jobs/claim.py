from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from assistant_platform.storage.models import BackgroundJobRow

_SESSION_LOCK_JOB_TYPES = frozenset({"session.process", "session.close"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _begin_claim_transaction(db_session: Session) -> None:
    """SQLite needs IMMEDIATE to serialize concurrent claim attempts."""
    if db_session.get_bind().dialect.name == "sqlite":
        db_session.execute(text("BEGIN IMMEDIATE"))


def claim_next_job(
    db_session: Session,
    *,
    blocked_session_ids: set[str],
) -> BackgroundJobRow | None:
    """Claim the oldest pending job, skipping sessions with an active process/close job."""
    _begin_claim_transaction(db_session)
    try:
        jobs = db_session.scalars(
            select(BackgroundJobRow)
            .where(BackgroundJobRow.status == "pending")
            .order_by(BackgroundJobRow.created_at.asc())
            .limit(100)
        ).all()
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
