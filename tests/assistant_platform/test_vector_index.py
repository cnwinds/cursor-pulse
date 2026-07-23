from __future__ import annotations

from datetime import datetime, timezone

from assistant_platform.memory.embedding import HashingEmbedder

from assistant_platform.memory.archive_models import ArchiveChunkRow
from assistant_platform.memory.vector_index import LocalVectorIndex, VectorRecord
from assistant_platform.storage.db import init_assistant_db


def test_local_vector_index_upsert_search_and_delete():
    Session = init_assistant_db("sqlite://")
    db = Session()
    embedder = HashingEmbedder(dimensions=64)
    index = LocalVectorIndex(db, embedder=embedder)

    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    specs = [
        ("c1", "s1", "u1", 0, "blue project deadline next week", "h1"),
        ("c2", "s1", "u1", 1, "unrelated weather chat", "h2"),
        ("c3", "s2", "u2", 0, "blue sky painting", "h3"),
    ]
    for chunk_id, session_id, subject_id, chunk_index, text, digest in specs:
        db.add(
            ArchiveChunkRow(
                id=chunk_id,
                session_id=session_id,
                team_id="t1",
                scope="personal",
                subject_id=subject_id,
                chunk_index=chunk_index,
                start_seq=1,
                end_seq=1,
                text=text,
                content_hash=digest,
                source_roles_json=["user"],
                source_message_ids_json=[],
                occurred_from=now,
                occurred_to=now,
                index_version=1,
                token_count=4,
            )
        )
    db.flush()
    records = [
        VectorRecord(
            chunk_id=chunk_id,
            session_id=session_id,
            team_id="t1",
            subject_id=subject_id,
            scope="personal",
            text=text,
            content_hash=digest,
            occurred_at=now,
        )
        for chunk_id, session_id, subject_id, _chunk_index, text, digest in specs
    ]
    index.upsert(records)
    db.commit()

    hits = index.search(
        "blue project deadline",
        team_id="t1",
        subject_id="u1",
        scope="personal",
        top_k=2,
    )
    assert hits
    assert hits[0].chunk_id == "c1"
    assert all(h.subject_id == "u1" for h in hits)

    index.delete_by_session("s1")
    db.commit()
    remaining = index.search("blue", team_id="t1", subject_id="u1", scope="personal", top_k=5)
    assert all(h.session_id != "s1" for h in remaining)
    db.close()
