"""SQLite multi-user chat hardening: lock recovery + short write windows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError, PendingRollbackError
from sqlalchemy.orm import Session

from assistant_platform.config import AssistantLlmConfig
from assistant_platform.conversation.agent_trace import persist_agent_trace_event
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.conversation.turn_recovery import recover_stale_processing_jobs
from assistant_platform.jobs.db_errors import is_retryable_db_lock_error
from assistant_platform.jobs.worker import finalize_job_after_failure
from assistant_platform.memory.archive_indexer import index_archived_session
from assistant_platform.memory.archive_pipeline import run_archive_pipeline
from assistant_platform.storage.db import init_assistant_db, make_engine
from assistant_platform.storage.repository import AssistantRepository

# Reuse archive pipeline fixtures helpers
from tests.assistant_platform.test_archive_pipeline import TEAM_ID, _closed_session, _config


def test_busy_timeout_is_at_least_30s():
    engine = make_engine("sqlite://")
    with engine.connect() as conn:
        value = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert int(value) >= 30000


def test_job_processing_timeout_default_is_90s():
    assert AssistantLlmConfig().job_processing_timeout_seconds == 90


def test_is_retryable_db_lock_error_detects_locked_and_pending_rollback():
    locked = OperationalError("stmt", {}, Exception("database is locked"))
    assert is_retryable_db_lock_error(locked) is True

    pending = PendingRollbackError(
        "This Session's transaction has been rolled back due to a previous "
        "exception during flush. Original exception was: (sqlite3.OperationalError) "
        "database is locked",
        [],
        None,
    )
    assert is_retryable_db_lock_error(pending) is True

    other = OperationalError("stmt", {}, Exception("no such table: x"))
    assert is_retryable_db_lock_error(other) is False


def test_finalize_job_after_failure_requeues_on_lock_error():
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    repo = AssistantRepository(db)
    job = repo.add_job(
        job_type="session.process",
        payload={"session_id": "s1", "message_id": "m1"},
    )
    job.status = "processing"
    db.commit()

    finalize_job_after_failure(
        db,
        job,
        OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked")),
        max_attempts=5,
    )
    db.commit()
    db.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 1


def test_finalize_job_after_failure_marks_failed_after_max_attempts():
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    repo = AssistantRepository(db)
    job = repo.add_job(
        job_type="session.process",
        payload={"session_id": "s1", "message_id": "m1"},
    )
    job.status = "processing"
    job.attempts = 4
    db.commit()

    finalize_job_after_failure(
        db,
        job,
        OperationalError("x", {}, Exception("database is locked")),
        max_attempts=5,
    )
    db.commit()
    db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 5


def test_finalize_job_after_failure_requeues_non_lock_session_process():
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    repo = AssistantRepository(db)
    job = repo.add_job(
        job_type="session.process",
        payload={"session_id": "s1", "message_id": "m1"},
    )
    job.status = "processing"
    db.commit()

    finalize_job_after_failure(db, job, RuntimeError("boom"), max_attempts=5)
    db.commit()
    db.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 1


def test_recover_stale_processing_jobs_at_90s():
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    repo = AssistantRepository(db)
    job = repo.add_job(
        job_type="session.process",
        payload={"session_id": "s1", "message_id": "m1"},
    )
    job.status = "processing"
    job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=91)
    db.commit()

    assert recover_stale_processing_jobs(db, timeout_seconds=90) == 1
    db.commit()
    db.refresh(job)
    assert job.status == "pending"


def test_persist_trace_failure_rolls_back_so_session_stays_usable(monkeypatch):
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    session_row = ChatSessionRow(
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="open",
    )
    db.add(session_row)
    db.commit()

    real_flush = Session.flush

    def boom(self, *args, **kwargs):
        if getattr(self, "_boom_once", False):
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return real_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", boom)
    db._boom_once = True
    with pytest.raises(OperationalError):
        persist_agent_trace_event(
            db,
            session_row=session_row,
            event={"type": "context", "skills": [], "tools": []},
        )
    # After failure helper must leave session usable for a follow-up write.
    db._boom_once = False
    session_row.status = "open"
    db.add(session_row)
    db.commit()  # must not raise PendingRollbackError


def test_archive_pipeline_commits_between_stages():
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    config = _config()
    session_row = _closed_session(db, text="事实: 使用 Opus")

    commits = {"n": 0}

    @event.listens_for(db.bind, "commit")
    def _count(_conn):
        commits["n"] += 1

    before = commits["n"]
    run_archive_pipeline(db, config=config, session_row=session_row)
    after = commits["n"]
    # Five stages ⇒ at least one commit boundary per stage (PARTIAL + READY, or per-stage).
    assert after - before >= 5


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self.during_write = False
        self.calls_during_write = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self.during_write:
            self.calls_during_write += 1
        # stable tiny vector
        return [float(len(text) % 7), 1.0, 0.0]


def test_index_embeds_before_write_burst(monkeypatch):
    SessionLocal = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = SessionLocal()
    config = _config()
    session_row = _closed_session(db, text="偏好: 简洁回复")
    from assistant_platform.memory.archive_pipeline import run_archive_pipeline_stage
    from assistant_platform.memory.contracts import ArchivePipelineStage

    run_archive_pipeline_stage(
        db, config=config, session_row=session_row, stage=ArchivePipelineStage.ARCHIVE
    )
    db.commit()

    embedder = _CountingEmbedder()
    real_add = db.add

    def tracking_add(obj):
        # Once ORM writes start for chunks, embedder must already be done.
        from assistant_platform.memory.archive_models import ArchiveChunkRow

        if isinstance(obj, ArchiveChunkRow):
            embedder.during_write = True
        return real_add(obj)

    monkeypatch.setattr(db, "add", tracking_add)
    index_archived_session(
        db,
        session_row,
        embedding_enabled=True,
        embedder=embedder,  # type: ignore[arg-type]
    )
    assert embedder.calls >= 1
    assert embedder.calls_during_write == 0
