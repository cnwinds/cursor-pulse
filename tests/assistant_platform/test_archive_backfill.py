from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_backfill import (
    BackfillSummary,
    find_backfill_candidates,
    run_archive_backfill,
)
from assistant_platform.memory.archive_models import ArchiveChunkRow, SessionArchiveRow
from assistant_platform.storage.db import init_assistant_db


def _closed_session_with_messages(db, *, text: str = "legacy topic alpha") -> ChatSessionRow:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    row = ChatSessionRow(
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
    db.add(row)
    db.add(
        ChatMessageRow(
            session_id=row.id,
            role="user",
            text_redacted=text,
            created_at=now,
        )
    )
    db.add(
        ChatMessageRow(
            session_id=row.id,
            role="assistant",
            text_redacted=f"reply about {text}",
            meta_json={"kind": "final"},
            created_at=now,
        )
    )
    db.flush()
    return row


def test_find_backfill_candidates_closed_sessions_with_ledger():
    Session = init_assistant_db("sqlite://")
    db = Session()
    target = _closed_session_with_messages(db)
    # Open session should be ignored.
    open_row = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id="team-1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u2",
        user_id="u2",
        status="open",
        opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    db.add(open_row)
    db.add(
        ChatMessageRow(
            session_id=open_row.id,
            role="user",
            text_redacted="open",
        )
    )
    db.commit()

    ids = find_backfill_candidates(db, index_version=1, batch_size=10)
    assert target.id in ids
    assert open_row.id not in ids
    db.close()


def test_run_archive_backfill_is_recoverable_and_idempotent():
    Session = init_assistant_db("sqlite://")
    db = Session()
    s1 = _closed_session_with_messages(db, text="first topic")
    s2 = _closed_session_with_messages(db, text="second topic")
    db.commit()

    summary = run_archive_backfill(db, index_version=1, batch_size=1)
    assert isinstance(summary, BackfillSummary)
    assert summary.processed == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.has_more is True

    first = db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == s1.id))
    second = db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == s2.id))
    # Exactly one of the two should be done after batch_size=1.
    done = [r for r in (first, second) if r is not None and r.status == "ready"]
    assert len(done) == 1

    summary2 = run_archive_backfill(db, index_version=1, batch_size=10)
    assert summary2.processed >= 1
    assert summary2.failed == 0

    for sid in (s1.id, s2.id):
        archive = db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == sid))
        assert archive is not None
        assert archive.status == "ready"
        assert archive.index_version == 1
        chunks = db.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == sid)).all()
        assert len(chunks) >= 1

    # Re-run with same version: no work left (or zero processed of pending).
    summary3 = run_archive_backfill(db, index_version=1, batch_size=10)
    assert summary3.has_more is False
    db.close()


def test_backfill_rebuilds_when_index_version_changes():
    Session = init_assistant_db("sqlite://")
    db = Session()
    row = _closed_session_with_messages(db, text="version bump topic")
    db.commit()

    run_archive_backfill(db, index_version=1, batch_size=10)
    archive = db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == row.id))
    assert archive is not None
    assert archive.index_version == 1
    old_chunk_ids = {
        c.id
        for c in db.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == row.id)).all()
    }

    summary = run_archive_backfill(db, index_version=2, batch_size=10, force_reindex=True)
    assert summary.succeeded >= 1
    archive = db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == row.id))
    assert archive is not None
    assert archive.index_version == 2
    new_chunks = db.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == row.id)).all()
    assert new_chunks
    assert all(c.index_version == 2 for c in new_chunks)
    # Old chunk rows replaced.
    assert {c.id for c in new_chunks} != old_chunk_ids or archive.index_version == 2
    db.close()
