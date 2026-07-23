from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_CHAT_SESSION_COLUMNS: dict[str, str] = {
    "session_state_json": "JSON DEFAULT '{}'",
}

_ARCHIVE_FTS_TABLE = "ap_archive_chunks_fts"
_ARCHIVE_FTS_TOKENIZER = "trigram"

_ARCHIVE_FTS_DDL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {_ARCHIVE_FTS_TABLE} USING fts5(
    text,
    chunk_id UNINDEXED,
    session_id UNINDEXED,
    team_id UNINDEXED,
    subject_id UNINDEXED,
    scope UNINDEXED,
    tokenize='{_ARCHIVE_FTS_TOKENIZER}'
)
"""


def _archive_fts_create_sql(conn) -> str | None:
    row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name = :name"),
        {"name": _ARCHIVE_FTS_TABLE},
    ).first()
    return row[0] if row else None


def _archive_fts_needs_rebuild(existing_sql: str | None) -> bool:
    if existing_sql is None:
        return False
    return f"tokenize='{_ARCHIVE_FTS_TOKENIZER}'" not in existing_sql


def _ensure_columns(engine: Engine, table_name: str, columns: dict[str, str]) -> set[str]:
    """Add any missing columns; return the set of columns that were newly created."""
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return set()
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    added: set[str] = set()
    with engine.begin() as conn:
        for col_name, col_type in columns.items():
            if col_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
            added.add(col_name)
            logger.info("Added column %s.%s", table_name, col_name)
    return added


def _ensure_archive_fts(engine: Engine) -> None:
    """Create FTS5 virtual table for archive chunk search (idempotent)."""
    with engine.begin() as conn:
        existing_sql = _archive_fts_create_sql(conn)
        if _archive_fts_needs_rebuild(existing_sql):
            conn.execute(text(f"DROP TABLE IF EXISTS {_ARCHIVE_FTS_TABLE}"))
            logger.info(
                "Dropped legacy FTS5 table %s for tokenizer rebuild (%s)",
                _ARCHIVE_FTS_TABLE,
                _ARCHIVE_FTS_TOKENIZER,
            )
        conn.execute(text(_ARCHIVE_FTS_DDL))
    logger.info("Ensured FTS5 table %s (tokenize=%s)", _ARCHIVE_FTS_TABLE, _ARCHIVE_FTS_TOKENIZER)


_CHAT_MESSAGE_COLUMNS: dict[str, str] = {
    "handled_at": "DATETIME",
}


_PROFILE_SIGNAL_COLUMNS: dict[str, str] = {
    "dimension": "VARCHAR(32) DEFAULT ''",
    "explicitness": "VARCHAR(16) DEFAULT 'inferred'",
    "status": "VARCHAR(16) DEFAULT 'active'",
    "evidence_json": "JSON DEFAULT '{}'",
    "superseded_by_id": "VARCHAR(36)",
}

_PROFILE_CORRECTION_COLUMNS: dict[str, str] = {
    "dimension": "VARCHAR(32) DEFAULT ''",
}


def migrate_assistant_schema(engine: Engine) -> None:
    """Lightweight SQLite migrations for existing assistant.db files."""
    # Ensure archive ORM tables exist even when callers only run migrate.
    from assistant_platform.memory.archive_models import (
        ArchiveChunkRow,
        ArchiveMessageRow,
        SessionArchiveRow,
    )
    from assistant_platform.memory.opt_out import MemoryOptOutRow
    from assistant_platform.memory.session_summary import SessionSummaryRow
    from assistant_platform.memory.semantic.migrate import migrate_pm_to_semantic
    from assistant_platform.memory.semantic.models import (
        CommitmentRow,
        DisclosureLogRow,
        SemanticAtomRow,
    )
    from assistant_platform.profiles.models import ProfileEffectiveRow, ProfileSignalRow
    from assistant_platform.storage.models import Base

    Base.metadata.create_all(
        engine,
        tables=[
            SessionArchiveRow.__table__,
            ArchiveMessageRow.__table__,
            ArchiveChunkRow.__table__,
            SessionSummaryRow.__table__,
            ProfileEffectiveRow.__table__,
            ProfileSignalRow.__table__,
            MemoryOptOutRow.__table__,
            SemanticAtomRow.__table__,
            CommitmentRow.__table__,
            DisclosureLogRow.__table__,
        ],
    )
    _ensure_archive_fts(engine)
    migrate_pm_to_semantic(engine)

    _ensure_columns(engine, "ap_profile_signals", _PROFILE_SIGNAL_COLUMNS)
    _ensure_columns(engine, "ap_profile_corrections", _PROFILE_CORRECTION_COLUMNS)

    _ensure_columns(engine, "ap_chat_sessions", _CHAT_SESSION_COLUMNS)
    added_msg_columns = _ensure_columns(engine, "ap_chat_messages", _CHAT_MESSAGE_COLUMNS)
    if "handled_at" in added_msg_columns:
        # One-time backfill: every message that existed before this column was
        # introduced was already processed by the legacy flow. Mark them handled
        # so they are not mistaken for pending mid-turn messages after restart.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE ap_chat_messages "
                    "SET handled_at = created_at "
                    "WHERE handled_at IS NULL"
                )
            )
        logger.info("Backfilled ap_chat_messages.handled_at for pre-existing rows")
