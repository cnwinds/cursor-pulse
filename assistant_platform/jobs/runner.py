from __future__ import annotations

import logging
import threading

from assistant_platform.config import AssistantConfig
from assistant_platform.jobs.worker import JobWorkerPool

logger = logging.getLogger(__name__)


def start_job_workers(
    session_factory,
    config: AssistantConfig,
    stop_event: threading.Event,
) -> JobWorkerPool:
    pool = JobWorkerPool(session_factory, config, stop_event)
    pool.start()
    return pool
