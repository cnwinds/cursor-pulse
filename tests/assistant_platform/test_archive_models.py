from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect, select

from assistant_platform.memory.archive_models import (
    ArchiveChunkRow,
    ArchiveMessageRow,
    SessionArchiveRow,
    resolve_archive_scope,
)
from assistant_platform.memory.contracts import MemoryScope
from assistant_platform.storage.db import init_assistant_db


def test_resolve_archive_scope_personal_vs_group():
    personal = resolve_archive_scope(
        conversation_type="private",
        user_id="u1",
        conversation_id="c1",
    )
    assert personal == (MemoryScope.PERSONAL, "u1")

    group = resolve_archive_scope(
        conversation_type="group",
        user_id="u1",
        conversation_id="g-42",
    )
    assert group == (MemoryScope.GROUP, "g-42")


def test_archive_tables_created_by_init_db():
    Session = init_assistant_db("sqlite://")
    session = Session()
    engine = session.get_bind()
    tables = set(inspect(engine).get_table_names())
    assert "ap_session_archives" in tables
    assert "ap_archive_messages" in tables
    assert "ap_archive_chunks" in tables
    session.close()


def test_session_archive_row_roundtrip():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    row = SessionArchiveRow(
        session_id="s1",
        team_id="team-1",
        scope=MemoryScope.PERSONAL.value,
        subject_id="u1",
        assistant_id="xiaomai",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="ready",
        archive_status="ready",
        index_status="ready",
        index_version=1,
        message_total=2,
        chunk_total=1,
        occurred_from=now,
        occurred_to=now,
        content_hash="abc",
        archived_at=now,
        indexed_at=now,
    )
    session.add(row)
    session.add(
        ArchiveMessageRow(
            session_id="s1",
            seq=1,
            source_message_id="m1",
            role="user",
            text_redacted="hello",
            content_hash="h1",
            created_at=now,
        )
    )
    session.add(
        ArchiveChunkRow(
            id="chunk-1",
            session_id="s1",
            team_id="team-1",
            scope=MemoryScope.PERSONAL.value,
            subject_id="u1",
            chunk_index=0,
            start_seq=1,
            end_seq=1,
            text="hello",
            content_hash="h1",
            source_roles_json=["user"],
            source_message_ids_json=["m1"],
            occurred_from=now,
            occurred_to=now,
            index_version=1,
            token_count=1,
        )
    )
    session.commit()

    loaded = session.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == "s1"))
    assert loaded is not None
    assert loaded.scope == "personal"
    assert loaded.message_total == 2
    msgs = session.scalars(select(ArchiveMessageRow).where(ArchiveMessageRow.session_id == "s1")).all()
    assert len(msgs) == 1
    chunks = session.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == "s1")).all()
    assert len(chunks) == 1
    assert chunks[0].id == "chunk-1"
    session.close()
