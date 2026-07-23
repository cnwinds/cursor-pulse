from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from assistant_platform.config import AssistantChatMemoryConfig, MemoryFeatureFlags, MemoryRecallBudgetConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_indexer import archive_and_index_session, estimate_tokens
from assistant_platform.memory.context_builder import build_recall_bundle, format_recall_block
from assistant_platform.memory.archive_search import resolve_search_scope
from assistant_platform.profiles.models import ProfileSignalRow
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.memory.semantic.domain import AtomKind, SourceVisibility, VisibilityContext
from assistant_platform.memory.semantic.models import SemanticAtomRow
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository


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


def test_build_recall_bundle_degrades_on_recall_timeout():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    db.add(session_row)
    db.add(_msg(session_row.id, "user", "timeout keyword detail", offset=1))
    db.add(_msg(session_row.id, "assistant", "ok", kind="final", offset=2))
    db.commit()
    archive_and_index_session(db, session_row, index_version=1)
    db.commit()

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )

    def slow_fts(*args, **kwargs):
        time.sleep(0.2)
        return []

    config = AssistantChatMemoryConfig(
        recall=MemoryRecallBudgetConfig(fragment_top_k=3, timeout_ms=50, context_token_budget=500),
        features=MemoryFeatureFlags(profile_compile=False),
    )
    with patch("assistant_platform.memory.archive_search._fts_search", side_effect=slow_fts):
        bundle = build_recall_bundle(
            db,
            query="timeout keyword",
            scope=scope,
            config=config,
            visibility_context=VisibilityContext.private("user-a"),
            include_profile=False,
        )
    assert bundle.degraded is True
    assert bundle.degrade_reason == "recall_timeout"
    assert bundle.fragments == ()


def test_build_recall_bundle_respects_token_budget():
    Session = init_assistant_db("sqlite://")
    db = Session()
    for idx in range(4):
        session_row = _session_row(id=str(uuid.uuid4()))
        db.add(session_row)
        long_text = "budget keyword " + ("token " * 80) + str(idx)
        for message in [
            _msg(session_row.id, "user", long_text, offset=1),
            _msg(session_row.id, "assistant", "ack " + str(idx), kind="final", offset=2),
        ]:
            db.add(message)
        db.commit()
        archive_and_index_session(db, session_row, index_version=1)
        db.commit()

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    config = AssistantChatMemoryConfig(
        recall=MemoryRecallBudgetConfig(
            fragment_top_k=4,
            max_fragments_per_session=2,
            context_token_budget=120,
        ),
        features=MemoryFeatureFlags(profile_compile=False),
    )
    bundle = build_recall_bundle(
        db,
        query="budget keyword",
        scope=scope,
        config=config,
        visibility_context=VisibilityContext.private("user-a"),
        include_profile=False,
    )
    assert bundle.token_estimate <= config.recall.context_token_budget
    assert len(bundle.fragments) >= 1
    assert "archive" in bundle.recall_sources or "fts" in bundle.recall_sources


def test_build_recall_bundle_includes_facts_and_profile():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    db.add(session_row)
    db.add(_msg(session_row.id, "user", "remember emerald project", offset=1))
    db.add(_msg(session_row.id, "assistant", "sure", kind="final", offset=2))
    db.commit()
    archive_and_index_session(db, session_row, index_version=1)
    db.commit()

    now = datetime.now(timezone.utc)
    db.add(
        SemanticAtomRow(
            id=str(uuid.uuid4()),
            namespace="team:team-a",
            subject_id="user-a",
            kind=AtomKind.FACT.value,
            content="User prefers concise replies",
            source_visibility=SourceVisibility.PRIVATE.value,
            sensitivity="public",
            confidence=0.9,
            created_at=now,
            last_seen_at=now,
        )
    )
    db.add(
        ProfileSignalRow(
            user_id="user-a",
            team_id="team-a",
            kind="preference",
            dimension="verbosity",
            content="Prefer short answers",
            confidence=0.85,
            explicitness="explicit",
            status="active",
        )
    )
    db.commit()

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    config = AssistantChatMemoryConfig(
        recall=MemoryRecallBudgetConfig(fragment_top_k=3, fact_top_k=3, context_token_budget=2000),
        features=MemoryFeatureFlags(profile_compile=True),
    )
    bundle = build_recall_bundle(
        db,
        query="emerald",
        scope=scope,
        config=config,
        visibility_context=VisibilityContext.private("user-a"),
        memory_repo=SemanticMemoryRepository(db),
        include_profile=True,
    )
    assert bundle.fragments or bundle.facts
    assert bundle.facts
    assert bundle.profile is not None
    assert bundle.profile.items


def test_format_recall_block_is_low_priority_section():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row()
    db.add(session_row)
    db.add(_msg(session_row.id, "user", "orchid flower detail", offset=1))
    db.add(_msg(session_row.id, "assistant", "noted", kind="final", offset=2))
    db.commit()
    archive_and_index_session(db, session_row, index_version=1)
    db.commit()

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    bundle = build_recall_bundle(
        db,
        query="orchid",
        scope=scope,
        config=AssistantChatMemoryConfig(
            recall=MemoryRecallBudgetConfig(context_token_budget=500),
            features=MemoryFeatureFlags(profile_compile=False),
        ),
        visibility_context=VisibilityContext.private("user-a"),
        include_profile=False,
    )
    block = format_recall_block(bundle)
    assert "历史记忆" in block or "召回" in block
    assert "orchid" in block.lower() or "flower" in block.lower()
    assert estimate_tokens(block) > 0
