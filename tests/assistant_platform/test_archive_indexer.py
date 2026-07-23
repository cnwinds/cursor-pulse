from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_indexer import (
    archive_and_index_session,
    build_indexable_chunks,
    estimate_tokens,
    is_indexable_message,
)
from assistant_platform.memory.archive_models import ArchiveChunkRow, ArchiveMessageRow, SessionArchiveRow
from assistant_platform.storage.db import init_assistant_db


def _session_row(**overrides) -> ChatSessionRow:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    data = dict(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id="team-1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="closed",
        opened_at=now,
        last_activity_at=now,
        closed_at=now,
    )
    data.update(overrides)
    return ChatSessionRow(**data)


def _msg(
    session_id: str,
    role: str,
    text: str,
    *,
    kind: str | None = None,
    created_at=None,
    seq_offset: int = 0,
) -> ChatMessageRow:
    base = created_at or datetime(2026, 7, 1, tzinfo=timezone.utc)
    if created_at is None and seq_offset:
        from datetime import timedelta

        base = base + timedelta(seconds=seq_offset)
    return ChatMessageRow(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role=role,
        text_redacted=text,
        meta_json={"kind": kind} if kind is not None else {},
        created_at=base,
    )


def test_is_indexable_message_filters_interim_and_tools():
    assert is_indexable_message(_msg("s", "user", "hi")) is True
    assert is_indexable_message(_msg("s", "assistant", "ok", kind="final")) is True
    assert is_indexable_message(_msg("s", "assistant", "thinking", kind="interim")) is False
    assert is_indexable_message(_msg("s", "tool", "result")) is False
    assert is_indexable_message(_msg("s", "system", "sys")) is False
    # Legacy assistant without kind is treated as final.
    assert is_indexable_message(_msg("s", "assistant", "legacy")) is True


def test_build_indexable_chunks_pairs_user_and_final_assistant():
    messages = [
        _msg("s", "user", "question one", seq_offset=1),
        _msg("s", "assistant", "scratch", kind="interim", seq_offset=2),
        _msg("s", "assistant", "answer one", kind="final", seq_offset=3),
        _msg("s", "user", "question two", seq_offset=4),
        _msg("s", "assistant", "answer two", kind="final", seq_offset=5),
    ]
    chunks = build_indexable_chunks(messages, max_tokens_per_chunk=512, overlap_tokens=0)
    assert len(chunks) == 2
    assert chunks[0].start_seq == 1
    assert chunks[0].end_seq == 3
    assert "question one" in chunks[0].text
    assert "answer one" in chunks[0].text
    assert "scratch" not in chunks[0].text
    assert chunks[1].chunk_index == 1
    assert chunks[1].start_seq == 4
    assert chunks[1].end_seq == 5


def test_build_indexable_chunks_splits_oversized_turn():
    long_user = "word " * 200
    messages = [
        _msg("s", "user", long_user, seq_offset=1),
        _msg("s", "assistant", "short reply", kind="final", seq_offset=2),
    ]
    assert estimate_tokens(long_user) > 64
    chunks = build_indexable_chunks(messages, max_tokens_per_chunk=64, overlap_tokens=8)
    assert len(chunks) >= 2
    assert all(c.start_seq == 1 and c.end_seq == 2 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_archive_and_index_persists_all_messages_and_fts_chunks():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    db.add(session_row)
    msgs = [
        _msg(session_row.id, "user", "remember the blue project", seq_offset=1),
        _msg(session_row.id, "assistant", "noted interim", kind="interim", seq_offset=2),
        _msg(session_row.id, "assistant", "I will remember blue project", kind="final", seq_offset=3),
        _msg(session_row.id, "tool", "tool output", seq_offset=4),
    ]
    for m in msgs:
        db.add(m)
    db.commit()

    archive = archive_and_index_session(db, session_row, index_version=1)
    db.commit()

    assert archive.status == "ready"
    assert archive.archive_status == "ready"
    assert archive.index_status == "ready"
    assert archive.message_total == 4
    assert archive.chunk_total == 1

    archived_msgs = db.scalars(
        select(ArchiveMessageRow).where(ArchiveMessageRow.session_id == session_row.id).order_by(ArchiveMessageRow.seq)
    ).all()
    assert [m.role for m in archived_msgs] == ["user", "assistant", "assistant", "tool"]
    assert [m.seq for m in archived_msgs] == [1, 2, 3, 4]

    chunks = db.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_row.id)).all()
    assert len(chunks) == 1
    assert "blue project" in chunks[0].text
    assert "interim" not in chunks[0].text

    fts_hits = db.execute(
        text(
            "SELECT chunk_id FROM ap_archive_chunks_fts "
            "WHERE ap_archive_chunks_fts MATCH 'blue' AND session_id = :sid"
        ),
        {"sid": session_row.id},
    ).all()
    assert len(fts_hits) == 1
    assert fts_hits[0].chunk_id == chunks[0].id

    # Idempotent re-run keeps one archive row and one chunk set.
    archive_and_index_session(db, session_row, index_version=1)
    db.commit()
    assert db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)) is not None
    assert len(db.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_row.id)).all()) == 1
    db.close()


def test_archive_fts_finds_chinese_substring():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    db.add(session_row)
    msgs = [
        _msg(session_row.id, "user", "我们计划去苏州旅游，预算大约三千元", seq_offset=1),
        _msg(session_row.id, "assistant", "好的，已记录苏州旅游计划", kind="final", seq_offset=2),
    ]
    for m in msgs:
        db.add(m)
    db.commit()

    archive = archive_and_index_session(db, session_row, index_version=2)
    db.commit()
    assert archive.index_status == "ready"

    chunks = db.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_row.id)).all()
    assert len(chunks) == 1
    assert "苏州" in chunks[0].text

    fts_hits = db.execute(
        text(
            "SELECT chunk_id FROM ap_archive_chunks_fts "
            "WHERE ap_archive_chunks_fts MATCH '苏州旅游' AND session_id = :sid"
        ),
        {"sid": session_row.id},
    ).all()
    assert len(fts_hits) == 1
    assert fts_hits[0].chunk_id == chunks[0].id
    db.close()
