from __future__ import annotations

from sqlalchemy import inspect, text

from assistant_platform.storage.db import make_engine
from assistant_platform.storage.migrate import migrate_assistant_schema


def test_migrate_adds_session_state_json_to_legacy_table():
    engine = make_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ap_chat_sessions (id VARCHAR(36) PRIMARY KEY)"))
    migrate_assistant_schema(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("ap_chat_sessions")}
    assert "session_state_json" in cols
    migrate_assistant_schema(engine)


def test_migrate_backfills_handled_at_for_legacy_messages():
    engine = make_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE ap_chat_messages ("
                "id VARCHAR(36) PRIMARY KEY, "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO ap_chat_messages (id, created_at) "
                "VALUES ('m1', '2026-01-01 00:00:00'), ('m2', '2026-01-02 00:00:00')"
            )
        )

    migrate_assistant_schema(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("ap_chat_messages")}
    assert "handled_at" in cols
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, created_at, handled_at FROM ap_chat_messages ORDER BY id")
        ).all()
    assert all(r.handled_at is not None for r in rows)
    assert all(r.handled_at == r.created_at for r in rows)

    # Idempotent: a second run must not touch anything (no new NULLs to backfill).
    migrate_assistant_schema(engine)


def test_migrate_pm_atoms_with_null_evidence_json():
    engine = make_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE pm_memory_atoms ("
                "id VARCHAR(36) PRIMARY KEY, "
                "namespace VARCHAR(128) NOT NULL, "
                "subject_id VARCHAR(128) NOT NULL, "
                "kind VARCHAR(16) NOT NULL, "
                "content TEXT NOT NULL, "
                "source_visibility VARCHAR(16) NOT NULL, "
                "sensitivity VARCHAR(16) NOT NULL, "
                "confidence FLOAT NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "last_seen_at DATETIME NOT NULL, "
                "first_confirmed_at DATETIME, "
                "supersedes_id VARCHAR(36), "
                "status VARCHAR(16) NOT NULL, "
                "evidence_json JSON, "
                "embedding_json JSON)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO pm_memory_atoms "
                "(id, namespace, subject_id, kind, content, source_visibility, "
                "sensitivity, confidence, created_at, last_seen_at, status, evidence_json) "
                "VALUES "
                "('a1', 'team-1', 'u1', 'fact', 'legacy fact', 'private', "
                "'confidential', 1.0, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'active', NULL)"
            )
        )

    migrate_assistant_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT evidence_json FROM ap_semantic_atoms WHERE id = 'a1'")
        ).one()
    assert row.evidence_json == "{}"
