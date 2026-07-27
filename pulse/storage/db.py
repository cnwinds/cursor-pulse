from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.storage.models import Base


def _is_memory_sqlite(database_url: str) -> bool:
    return database_url in ("sqlite://", "sqlite:///:memory:")


def make_engine(database_url: str):
    connect_args = {}
    engine_kwargs: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    if _is_memory_sqlite(database_url):
        # :memory: is per-connection unless the pool is shared.
        engine_kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            # WAL + relaxed sync avoids FULL fsync on every DDL/commit. create_all
            # on a file DB drops from ~15s to <1s under typical CI disks.
            # Do not enable foreign_keys=ON here: existing Pulse schema/tests
            # rely on SQLite's default (OFF) for dangling FK references.
            if not _is_memory_sqlite(database_url):
                cursor.execute("PRAGMA journal_mode=WAL")
                # OFF under pytest avoids fsync storms across many temp DBs.
                sync = "OFF" if "pytest" in sys.modules else "NORMAL"
                cursor.execute(f"PRAGMA synchronous={sync}")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def init_db(database_url: str) -> sessionmaker[Session]:
    if database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    from pulse.storage.migrate import migrate_schema

    migrate_schema(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
