from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.orchestrator import (
    process_session_close_job,
    process_session_job,
)
from assistant_platform.conversation.retention import purge_messages_older_than
from assistant_platform.conversation.turn_inbox import reschedule_session_after_turn
from assistant_platform.conversation.turn_recovery import (
    recover_stale_processing_jobs,
    recover_stale_turns,
)
from assistant_platform.jobs.claim import claim_next_job
from assistant_platform.integrations.channel_reply import send_channel_reply

logger = logging.getLogger(__name__)

_RETENTION_INTERVAL_SECONDS = 6 * 60 * 60


def _handle_reply_send(payload: dict, config: AssistantConfig) -> None:
    send_channel_reply(payload, config)


def _run_job(session, job, config: AssistantConfig) -> None:
    if job.job_type == "session.process":
        process_session_job(session, job.payload_json, config)
    elif job.job_type == "session.close":
        process_session_close_job(session, job.payload_json, config)
    elif job.job_type == "reply.send":
        _handle_reply_send(job.payload_json, config)
    elif job.job_type == "noop.phase0":
        pass
    else:
        logger.warning("unknown assistant job type: %s", job.job_type)


class JobWorkerPool:
    """Job workers. Default 1 for SQLite; multi-worker needs Postgres for reliability."""

    def __init__(
        self,
        session_factory,
        config: AssistantConfig,
        stop_event: threading.Event,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._stop = stop_event
        self._lock = threading.Lock()
        self._active_sessions: set[str] = set()
        self._threads: list[threading.Thread] = []
        self._retention_lock = threading.Lock()
        self._last_retention_at = 0.0

    def start(self) -> None:
        count = max(1, self._config.llm.job_worker_count)
        for index in range(count):
            thread = threading.Thread(
                target=self._worker_loop,
                args=(index,),
                name=f"assistant-job-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        logger.info("Assistant job pool started with %s workers", count)

    def join(self, timeout: float | None = 2.0) -> None:
        for thread in self._threads:
            thread.join(timeout=timeout)

    def _worker_loop(self, worker_index: int) -> None:
        llm_cfg = self._config.llm
        while not self._stop.is_set():
            session = self._session_factory()
            session_id: str | None = None
            try:
                if worker_index == 0:
                    self._maybe_run_retention(session)

                stale_turns = recover_stale_turns(
                    session, timeout_seconds=llm_cfg.turn_timeout_seconds
                )
                stale_jobs = recover_stale_processing_jobs(
                    session, timeout_seconds=llm_cfg.job_processing_timeout_seconds
                )
                if stale_turns or stale_jobs:
                    session.commit()

                with self._lock:
                    blocked = set(self._active_sessions)
                job = claim_next_job(session, blocked_session_ids=blocked)
                if job is None:
                    session.close()
                    self._stop.wait(0.5)
                    continue

                if job.job_type in ("session.process", "session.close"):
                    raw_session_id = job.payload_json.get("session_id")
                    if raw_session_id:
                        session_id = str(raw_session_id)
                        with self._lock:
                            self._active_sessions.add(session_id)

                logger.info(
                    "reply.timing stage=job_claimed worker=%d job_type=%s job_id=%s "
                    "session_id=%s created_at=%s at=%s",
                    worker_index,
                    job.job_type,
                    job.id,
                    job.payload_json.get("session_id", ""),
                    job.created_at.isoformat() if job.created_at else "",
                    datetime.now(timezone.utc).isoformat(),
                )
                job_t0 = time.monotonic()
                _run_job(session, job, self._config)
                logger.info(
                    "reply.timing stage=job_done worker=%d job_type=%s job_id=%s "
                    "elapsed_ms=%d",
                    worker_index,
                    job.job_type,
                    job.id,
                    int((time.monotonic() - job_t0) * 1000),
                )
                job.status = "done"
                job.attempts = (job.attempts or 0) + 1
                session.commit()
                if job.job_type == "session.process" and session_id:
                    reschedule_session_after_turn(self._session_factory, session_id)
            except Exception:
                logger.exception("assistant job worker failed")
                session.rollback()
            finally:
                if session_id:
                    with self._lock:
                        self._active_sessions.discard(session_id)
                session.close()
            self._stop.wait(0.1)

    def _maybe_run_retention(self, session) -> None:
        now_mono = time.monotonic()
        with self._retention_lock:
            if now_mono - self._last_retention_at < _RETENTION_INTERVAL_SECONDS:
                return
            self._last_retention_at = now_mono
        deleted = purge_messages_older_than(session)
        if deleted:
            logger.info("retention purged %s expired chat messages", deleted)
        session.commit()
