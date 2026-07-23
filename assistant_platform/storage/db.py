from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import assistant_platform.capabilities.models  # noqa: F401 — register ORM tables on Base
import assistant_platform.conversation.models  # noqa: F401 — register chat session tables on Base
import assistant_platform.evaluation.models  # noqa: F401 — register evaluation tables on Base
import assistant_platform.evolution.models  # noqa: F401 — register evolution tables on Base
import assistant_platform.memory.archive_models  # noqa: F401 — register archive tables on Base
import assistant_platform.memory.opt_out  # noqa: F401 — register opt-out table on Base
import assistant_platform.memory.semantic.models  # noqa: F401 — register semantic memory tables on Base
import assistant_platform.memory.session_summary  # noqa: F401 — register summary table on Base
import assistant_platform.prompts.models  # noqa: F401 — register prompt tables on Base
import assistant_platform.profiles.models  # noqa: F401 — register profile signal tables on Base
import assistant_platform.review.models  # noqa: F401 — register review tables on Base
import assistant_platform.secrets.store  # noqa: F401 — register ap_secrets on Base
from assistant_platform.capabilities.seed import seed_phase1_capabilities
from assistant_platform.prompts.seed import ensure_production_prompt_release
from assistant_platform.review.seed import seed_review_rubrics
from assistant_platform.storage.migrate import migrate_assistant_schema
from assistant_platform.storage.models import Base

logger = logging.getLogger(__name__)


def _is_memory_sqlite(database_url: str) -> bool:
    return database_url in ("sqlite://", "sqlite:///:memory:")


def make_engine(database_url: str):
    connect_args = {}
    engine_kwargs: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    if _is_memory_sqlite(database_url):
        engine_kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            # Wait (ms) for a competing writer instead of failing instantly with
            # "database is locked"; short transactions keep the real wait tiny.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def init_assistant_db(database_url: str, *, team_id: str = "") -> sessionmaker[Session]:
    if database_url.startswith("sqlite:///"):
        Path(database_url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    migrate_assistant_schema(engine)
    effective_team_id = team_id or "default"
    seed_session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )()
    try:
        seed_phase1_capabilities(seed_session, effective_team_id)
        seed_review_rubrics(seed_session)
        ensure_production_prompt_release(seed_session)
        seed_session.commit()
    except Exception:
        seed_session.rollback()
        raise
    finally:
        seed_session.close()
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
