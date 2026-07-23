from __future__ import annotations

import logging
import os
import threading

import uvicorn

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig, load_assistant_config, validate_runtime_config
from assistant_platform.jobs.runner import start_job_workers
from assistant_platform.skills.vector_sync import start_skill_vector_sync
from assistant_platform.storage.db import init_assistant_db

logger = logging.getLogger(__name__)


def run_assistant(config: AssistantConfig | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    config = config or load_assistant_config()
    strict = os.environ.get("ASSISTANT_ENV", "").strip().lower() == "production"
    validate_runtime_config(config, strict=strict)
    session_factory = init_assistant_db(config.database_url, team_id=config.team_id)
    app = create_assistant_app(config, session_factory)
    stop = threading.Event()
    pool = start_job_workers(session_factory, config, stop)
    start_skill_vector_sync(session_factory, config, stop)
    logger.info("Assistant Platform listening on %s:%s", config.host, config.port)
    try:
        uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    finally:
        stop.set()
        pool.join(timeout=3)
