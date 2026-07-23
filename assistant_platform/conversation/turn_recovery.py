from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.conversation.turn_inbox import (
    end_turn,
    is_turn_running,
    try_schedule_next_turn,
    turn_started_at,
)
from assistant_platform.storage.models import BackgroundJobRow
from assistant_platform.storage.repository import AssistantRepository

logger = logging.getLogger(__name__)

_ACTIVE_JOB_TYPES = frozenset({"session.process", "session.close", "reply.send"})


def _session_has_processing_job(db_session: Session, session_id: str) -> bool:
    jobs = db_session.scalars(
        select(BackgroundJobRow).where(BackgroundJobRow.status == "processing")
    ).all()
    for job in jobs:
        if str(job.payload_json.get("session_id") or "") == session_id:
            if job.job_type in _ACTIVE_JOB_TYPES:
                return True
    return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def recover_stale_turn_if_needed(
    db_session: Session,
    session_row: ChatSessionRow,
    *,
    timeout_seconds: int,
) -> bool:
    """Reset a stale running turn on one session; enqueue follow-up if inbox remains."""
    if not is_turn_running(session_row):
        return False
    if _session_has_processing_job(db_session, session_row.id):
        return False
    started = turn_started_at(session_row)
    if started is None:
        return False
    age = (_utcnow() - started).total_seconds()
    if age <= timeout_seconds:
        return False
    return _reset_stale_turn(db_session, session_row)


def recover_stale_turns(db_session: Session, *, timeout_seconds: int) -> int:
    """Scan open sessions and reset turns that exceeded timeout."""
    rows = db_session.scalars(
        select(ChatSessionRow).where(ChatSessionRow.status == "open")
    ).all()
    recovered = 0
    for row in rows:
        if recover_stale_turn_if_needed(db_session, row, timeout_seconds=timeout_seconds):
            recovered += 1
    return recovered


def _reset_stale_turn(db_session: Session, session_row: ChatSessionRow) -> bool:
    logger.warning("recover stale turn session_id=%s", session_row.id)
    end_turn(db_session, session_row)
    db_session.add(session_row)
    db_session.flush()
    repo = AssistantRepository(db_session)
    repo.add_audit(
        assistant_id=session_row.assistant_id,
        team_id=session_row.team_id,
        action="turn.recovered.stale",
        detail=session_row.id,
    )
    try_schedule_next_turn(db_session, session_row, repo)
    return True


def recover_stale_processing_jobs(
    db_session: Session,
    *,
    timeout_seconds: int,
) -> int:
    """Re-queue jobs stuck in processing (worker crash)."""
    from assistant_platform.storage.models import BackgroundJobRow

    cutoff = _utcnow().timestamp() - timeout_seconds
    rows = db_session.scalars(
        select(BackgroundJobRow).where(BackgroundJobRow.status == "processing")
    ).all()
    recovered = 0
    for job in rows:
        updated = job.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated.timestamp() > cutoff:
            continue
        job.status = "pending"
        job.updated_at = _utcnow()
        db_session.add(job)
        recovered += 1
    if recovered:
        db_session.flush()
    return recovered
