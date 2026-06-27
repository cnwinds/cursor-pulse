from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from pulse.storage.models import Base, Team
from personamem.db import init_memory_tables

logger = logging.getLogger(__name__)

_TEAM_ID_TABLES = ("members", "metric_snapshots", "reports", "alert_logs")

_MEMBER_PORTAL_COLUMNS: dict[str, str] = {
    "portal_role": "VARCHAR(16)",
    "portal_permissions": "JSON",
    "password_hash": "VARCHAR(256)",
    "last_portal_login_at": "DATETIME",
}


def migrate_schema(engine: Engine) -> None:
    """轻量迁移：为已有 SQLite 库补齐 Phase 3 表与 team_id 列。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "teams" not in tables:
        Team.__table__.create(engine)
        logger.info("Created teams table")

    for table in _TEAM_ID_TABLES:
        if table not in tables:
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        if "team_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN team_id VARCHAR(36)"))
            logger.info("Added team_id column to %s", table)

    if "members" in tables:
        columns = {col["name"] for col in inspector.get_columns("members")}
        for col_name, col_type in _MEMBER_PORTAL_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE members ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to members", col_name)

    Base.metadata.create_all(engine)
    init_memory_tables(engine)
