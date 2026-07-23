"""Assistant Platform test defaults: never call remote embedding APIs."""

from __future__ import annotations

import pytest

from assistant_platform.memory.embedding import HashingEmbedder
from assistant_platform.prompts.models import PromptFragmentRow, PromptReleaseRow

_HASHING_MODEL = "hashing-embedder-v2"


@pytest.fixture(autouse=True)
def _force_hashing_embedder(monkeypatch):
    def _hashing(*_args, **_kwargs):
        return HashingEmbedder(), _HASHING_MODEL

    monkeypatch.setattr(
        "assistant_platform.memory.embedder.build_archive_embedder",
        _hashing,
    )
    monkeypatch.setattr(
        "assistant_platform.skills.vector_sync.build_archive_embedder",
        _hashing,
    )


def seed_test_production_release(session) -> PromptReleaseRow:
    """Create a minimal production prompt release for tests that still expect one."""
    from assistant_platform.prompts.seed import get_production_release

    existing = get_production_release(session)
    if existing is not None:
        return existing
    frag = PromptFragmentRow(key="precepts.md", content="test precepts", version="1", status="active")
    session.add(frag)
    session.flush()
    release = PromptReleaseRow(
        name="test-production",
        status="production",
        fragment_ids_json=[frag.id],
    )
    session.add(release)
    session.flush()
    return release


def final_assistant_message(session, *, session_id: str | None = None):
    """Latest user-visible assistant message (skip ledger_only agent_trace rows)."""
    from sqlalchemy import select

    from assistant_platform.conversation.models import ChatMessageRow

    q = select(ChatMessageRow).where(ChatMessageRow.role == "assistant")
    if session_id:
        q = q.where(ChatMessageRow.session_id == session_id)
    rows = list(session.scalars(q.order_by(ChatMessageRow.id.asc())))
    for row in reversed(rows):
        meta = row.meta_json or {}
        if meta.get("ledger_only"):
            continue
        if meta.get("kind") in ("context", "thinking", "tool"):
            continue
        return row
    return None
