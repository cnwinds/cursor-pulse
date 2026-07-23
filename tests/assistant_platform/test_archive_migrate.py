from __future__ import annotations

from sqlalchemy import inspect, text

from assistant_platform.storage.db import make_engine
from assistant_platform.storage.migrate import migrate_assistant_schema


def test_migrate_creates_archive_fts5_table_with_trigram():
    engine = make_engine("sqlite://")
    migrate_assistant_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert "ap_archive_chunks_fts" in tables

    with engine.begin() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'ap_archive_chunks_fts'")
        ).scalar_one()
        assert "trigram" in ddl

        conn.execute(
            text(
                "INSERT INTO ap_archive_chunks_fts"
                "(chunk_id, session_id, team_id, subject_id, scope, text) "
                "VALUES ('c1', 's1', 't1', 'u1', 'personal', 'hello archive world')"
            )
        )
        rows = conn.execute(
            text(
                "SELECT chunk_id FROM ap_archive_chunks_fts "
                "WHERE ap_archive_chunks_fts MATCH 'archive'"
            )
        ).all()
    assert [r.chunk_id for r in rows] == ["c1"]


def test_migrate_creates_archive_fts5_table():
    """Backward-compatible alias for trigram FTS creation."""
    test_migrate_creates_archive_fts5_table_with_trigram()


def test_migrate_rebuilds_legacy_unicode61_fts():
    engine = make_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE ap_archive_chunks_fts USING fts5("
                "text, chunk_id UNINDEXED, session_id UNINDEXED, "
                "team_id UNINDEXED, subject_id UNINDEXED, scope UNINDEXED, "
                "tokenize='unicode61'"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO ap_archive_chunks_fts"
                "(chunk_id, session_id, team_id, subject_id, scope, text) "
                "VALUES ('legacy', 's1', 't1', 'u1', 'personal', 'legacy unicode61 row')"
            )
        )

    migrate_assistant_schema(engine)

    with engine.begin() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'ap_archive_chunks_fts'")
        ).scalar_one()
        assert "trigram" in ddl
        assert "unicode61" not in ddl
        rows = conn.execute(
            text("SELECT chunk_id FROM ap_archive_chunks_fts WHERE ap_archive_chunks_fts MATCH 'legacy'")
        ).all()
    assert rows == []


def test_migrate_trigram_supports_chinese_substring_match():
    engine = make_engine("sqlite://")
    migrate_assistant_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ap_archive_chunks_fts"
                "(chunk_id, session_id, team_id, subject_id, scope, text) "
                "VALUES ('c-cn', 's1', 't1', 'u1', 'personal', "
                "'用户讨论了苏州旅游计划和预算安排')"
            )
        )
        rows = conn.execute(
            text(
                "SELECT chunk_id FROM ap_archive_chunks_fts "
                "WHERE ap_archive_chunks_fts MATCH '苏州旅游'"
            )
        ).all()
    assert [r.chunk_id for r in rows] == ["c-cn"]


def test_migrate_archive_fts_is_idempotent():
    engine = make_engine("sqlite://")
    migrate_assistant_schema(engine)
    migrate_assistant_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert "ap_archive_chunks_fts" in tables
