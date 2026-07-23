from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text

from assistant_platform.config import AssistantChatMemoryConfig, MemoryRecallBudgetConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_indexer import archive_and_index_session
from assistant_platform.memory.archive_models import ArchiveChunkRow, resolve_archive_scope
from assistant_platform.memory.contracts import ChunkAnchor, MemoryScope
from assistant_platform.memory.archive_search import (
    SearchScope,
    _fts_query_text,
    compute_query_fingerprint,
    expand_neighbors,
    hybrid_search,
    read_message_range,
    resolve_search_scope,
)
from assistant_platform.storage.db import init_assistant_db


def _session_row(**overrides) -> ChatSessionRow:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    data = dict(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id="team-a",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
        status="closed",
        opened_at=now,
        last_activity_at=now,
        closed_at=now,
    )
    data.update(overrides)
    return ChatSessionRow(**data)


def _msg(session_id: str, role: str, text: str, *, kind: str | None = None, offset: int = 0) -> ChatMessageRow:
    from datetime import timedelta

    base = datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)
    return ChatMessageRow(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role=role,
        text_redacted=text,
        meta_json={"kind": kind} if kind is not None else {},
        created_at=base,
    )


def _archive_session(db, session_row: ChatSessionRow, messages: list[ChatMessageRow]) -> None:
    db.add(session_row)
    for message in messages:
        db.add(message)
    db.commit()
    archive_and_index_session(db, session_row, index_version=1)
    db.commit()


def _chat_memory(**recall_overrides) -> AssistantChatMemoryConfig:
    recall = MemoryRecallBudgetConfig(**recall_overrides)
    return AssistantChatMemoryConfig(recall=recall)


def test_hybrid_search_fuses_fts_and_vector_ranking():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    messages = [
        _msg(session_row.id, "user", "alpha project deadline is Friday", offset=1),
        _msg(session_row.id, "assistant", "noted alpha project", kind="final", offset=2),
        _msg(session_row.id, "user", "beta release notes draft", offset=3),
        _msg(session_row.id, "assistant", "beta looks good", kind="final", offset=4),
    ]
    _archive_session(db, session_row, messages)

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    hits, page = hybrid_search(
        db,
        query="alpha project",
        scope=scope,
        config=_chat_memory(fragment_top_k=5, max_fragments_per_session=5),
    )
    assert page.total_hits >= 1
    assert hits
    assert hits[0].rank == 1
    assert "alpha project" in hits[0].text.lower()
    assert hits[0].session_message_total == 4
    assert hits[0].session_chunk_total >= 1
    assert hits[0].anchor.session_id == session_row.id


def test_cursor_pagination_is_stable():
    Session = init_assistant_db("sqlite://")
    db = Session()
    for idx in range(3):
        session_row = _session_row(id=str(uuid.uuid4()))
        messages = [
            _msg(session_row.id, "user", f"topic rocket launch number {idx}", offset=1),
            _msg(session_row.id, "assistant", f"rocket {idx} confirmed", kind="final", offset=2),
        ]
        _archive_session(db, session_row, messages)

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    config = _chat_memory(fragment_top_k=2, max_fragments_per_session=1)
    fingerprint = compute_query_fingerprint("rocket", scope)

    page1_hits, page1 = hybrid_search(db, query="rocket", scope=scope, config=config)
    assert page1.returned_count == len(page1_hits) <= 2
    assert page1.cursor is not None
    assert page1.cursor.query_fingerprint == fingerprint

    page2_hits, page2 = hybrid_search(
        db,
        query="rocket",
        scope=scope,
        config=config,
        cursor=page1.cursor,
    )
    assert page2_hits
    page1_ids = {hit.memory_id for hit in page1_hits}
    page2_ids = {hit.memory_id for hit in page2_hits}
    assert page1_ids.isdisjoint(page2_ids)

    # Re-run page 1 with same fingerprint — identical ordering and ids.
    replay_hits, replay_page = hybrid_search(db, query="rocket", scope=scope, config=config)
    assert [h.memory_id for h in replay_hits] == [h.memory_id for h in page1_hits]
    assert replay_page.cursor is not None
    assert page1.cursor is not None
    assert replay_page.cursor.query_fingerprint == page1.cursor.query_fingerprint
    assert replay_page.cursor.offset == page1.cursor.offset


def test_expand_neighbors_returns_prev_and_next():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    messages = [
        _msg(session_row.id, "user", "first turn about cats", offset=1),
        _msg(session_row.id, "assistant", "cats noted", kind="final", offset=2),
        _msg(session_row.id, "user", "second turn about dogs", offset=3),
        _msg(session_row.id, "assistant", "dogs noted", kind="final", offset=4),
        _msg(session_row.id, "user", "third turn about birds", offset=5),
        _msg(session_row.id, "assistant", "birds noted", kind="final", offset=6),
    ]
    _archive_session(db, session_row, messages)
    chunks = db.scalars(
        select(ArchiveChunkRow)
        .where(ArchiveChunkRow.session_id == session_row.id)
        .order_by(ArchiveChunkRow.chunk_index.asc())
    ).all()
    assert len(chunks) >= 2
    middle = chunks[1]
    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    anchor = ChunkAnchor(
        session_id=session_row.id,
        chunk_index=middle.chunk_index,
        start_seq=middle.start_seq,
        end_seq=middle.end_seq,
    )
    window = expand_neighbors(
        db,
        anchor=anchor,
        scope=scope,
        neighbor_count=1,
    )
    assert window.anchor == anchor
    assert window.prev_hits
    assert window.next_hits
    assert window.prev_hits[0].chunk_index < middle.chunk_index
    assert window.next_hits[0].chunk_index > middle.chunk_index
    assert window.prev_hits[0].has_prev is False or window.prev_hits[0].chunk_index > 0


def test_scope_isolation_blocks_other_team_and_subject():
    Session = init_assistant_db("sqlite://")
    db = Session()

    mine = _session_row(team_id="team-a", user_id="user-a", conversation_id="user-a")
    other_team = _session_row(
        id=str(uuid.uuid4()),
        team_id="team-b",
        user_id="user-a",
        conversation_id="user-a",
    )
    other_user = _session_row(
        id=str(uuid.uuid4()),
        team_id="team-a",
        user_id="user-b",
        conversation_id="user-b",
    )
    for row, keyword in ((mine, "secret-alpha"), (other_team, "secret-alpha"), (other_user, "secret-alpha")):
        messages = [
            _msg(row.id, "user", f"{keyword} token for isolation", offset=1),
            _msg(row.id, "assistant", "ok", kind="final", offset=2),
        ]
        _archive_session(db, row, messages)

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    hits, page = hybrid_search(
        db,
        query="secret-alpha",
        scope=scope,
        config=_chat_memory(fragment_top_k=10, max_fragments_per_session=5),
    )
    assert page.total_hits == 1
    assert len(hits) == 1
    assert hits[0].session_id == mine.id


def test_excludes_open_current_session():
    Session = init_assistant_db("sqlite://")
    db = Session()
    open_row = _session_row(status="open", closed_at=None)
    closed_row = _session_row(id=str(uuid.uuid4()))
    _archive_session(
        db,
        open_row,
        [
            _msg(open_row.id, "user", "open session mango detail", offset=1),
            _msg(open_row.id, "assistant", "ok", kind="final", offset=2),
        ],
    )
    _archive_session(
        db,
        closed_row,
        [
            _msg(closed_row.id, "user", "closed session mango detail", offset=1),
            _msg(closed_row.id, "assistant", "ok", kind="final", offset=2),
        ],
    )
    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
        exclude_session_id=open_row.id,
    )
    hits, _page = hybrid_search(
        db,
        query="mango",
        scope=scope,
        config=_chat_memory(fragment_top_k=5),
    )
    session_ids = {hit.session_id for hit in hits}
    assert open_row.id not in session_ids
    assert closed_row.id in session_ids


def test_read_range_respects_scope():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    messages = [
        _msg(session_row.id, "user", "line one", offset=1),
        _msg(session_row.id, "assistant", "line two", kind="final", offset=2),
        _msg(session_row.id, "user", "line three", offset=3),
    ]
    _archive_session(db, session_row, messages)
    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    allowed = read_message_range(
        db,
        session_id=session_row.id,
        start_seq=1,
        end_seq=2,
        scope=scope,
    )
    assert len(allowed) == 2
    assert allowed[0].start_seq == 1

    wrong_scope = resolve_search_scope(
        team_id="team-b",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    denied = read_message_range(
        db,
        session_id=session_row.id,
        start_seq=1,
        end_seq=2,
        scope=wrong_scope,
    )
    assert denied == []


def test_group_scope_uses_conversation_subject():
    scope, subject = resolve_archive_scope(
        conversation_type="group",
        user_id="user-a",
        conversation_id="group-chat-1",
    )
    assert scope == MemoryScope.GROUP
    assert subject == "group-chat-1"
    search_scope = resolve_search_scope(
        team_id="team-a",
        subject_id="member-1",
        conversation_type="group",
        conversation_id="group-chat-1",
        user_id="user-a",
    )
    assert search_scope.scope == MemoryScope.GROUP
    assert search_scope.subject_id == "group-chat-1"


def test_fts_query_text_handles_chinese_without_spaces():
    assert _fts_query_text("苏州旅游计划") == '"苏州旅游计划"'
    assert _fts_query_text("alpha project") == '"alpha" OR "project"'


def test_hybrid_search_finds_chinese_substring():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    messages = [
        _msg(session_row.id, "user", "我们计划去苏州旅游，预算大约三千元", offset=1),
        _msg(session_row.id, "assistant", "好的，已记录苏州旅游计划", kind="final", offset=2),
    ]
    _archive_session(db, session_row, messages)

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    hits, page = hybrid_search(
        db,
        query="苏州旅游",
        scope=scope,
        config=_chat_memory(fragment_top_k=5, max_fragments_per_session=5, fts_weight=1.0, vector_weight=0.0),
    )
    assert page.total_hits >= 1
    assert hits
    assert "苏州旅游" in hits[0].text
