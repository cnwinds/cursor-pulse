from __future__ import annotations

import uuid
from datetime import datetime, timezone

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.memory.archive_models import ArchiveChunkRow, ArchiveMessageRow
from assistant_platform.memory.contracts import MemoryScope
from assistant_platform.memory.session_summary import (
    build_session_summary_from_archive,
    summary_content_hash,
    upsert_session_summary,
)
from assistant_platform.storage.db import init_assistant_db


def _session_row() -> ChatSessionRow:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    return ChatSessionRow(
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


def test_build_session_summary_extracts_facts_preferences_and_evidence():
    session_row = _session_row()
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    messages = [
        ArchiveMessageRow(
            session_id=session_row.id,
            seq=1,
            role="user",
            text_redacted="事实: 项目代号 Pulse",
            created_at=now,
        ),
        ArchiveMessageRow(
            session_id=session_row.id,
            seq=2,
            role="user",
            text_redacted="偏好: 回复用列表",
            created_at=now,
        ),
        ArchiveMessageRow(
            session_id=session_row.id,
            seq=3,
            role="assistant",
            text_redacted="收到，后续用列表回复。",
            meta_json={"kind": "final"},
            created_at=now,
        ),
    ]
    chunks = [
        ArchiveChunkRow(
            id="chunk-1",
            session_id=session_row.id,
            team_id="team-1",
            scope=MemoryScope.PERSONAL.value,
            subject_id="u1",
            chunk_index=0,
            start_seq=1,
            end_seq=3,
            text="combined",
            occurred_from=now,
            occurred_to=now,
        )
    ]

    summary = build_session_summary_from_archive(session_row, messages, chunks, archived_at=now)
    assert summary.scope == MemoryScope.PERSONAL
    assert len(summary.facts) == 1
    assert summary.facts[0].content == "项目代号 Pulse"
    assert summary.facts[0].evidence[0].message_seq == 1
    assert len(summary.preferences) == 1
    assert summary.outcome.startswith("收到")


def test_upsert_session_summary_is_idempotent_on_same_content():
    Session = init_assistant_db("sqlite://")
    session = Session()
    session_row = _session_row()
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    messages = [
        ArchiveMessageRow(
            session_id=session_row.id,
            seq=1,
            role="user",
            text_redacted="偏好: 简洁",
            created_at=now,
        )
    ]
    summary = build_session_summary_from_archive(session_row, messages, [], archived_at=now)
    digest = summary_content_hash(summary)
    row1 = upsert_session_summary(session, summary, content_hash=digest)
    session.commit()
    row2 = upsert_session_summary(session, summary, content_hash=digest)
    session.commit()
    assert row1.id == row2.id
    session.close()
