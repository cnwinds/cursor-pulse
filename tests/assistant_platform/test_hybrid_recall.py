from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from assistant_platform.config import AssistantChatMemoryConfig, MemoryFeatureFlags, MemoryRecallBudgetConfig
from sqlalchemy import select

from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.memory.agent_tools import MemoryToolService
from assistant_platform.memory.archive_indexer import archive_and_index_session
from assistant_platform.memory.session_summary import generate_session_summary
from assistant_platform.memory.archive_search import resolve_search_scope
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow
from assistant_platform.memory.semantic.domain import VisibilityContext


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
        status="open",
        opened_at=now,
        last_activity_at=now,
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


def _incoming(team_id: str = "team-a", user_id: str = "user-a") -> IncomingEventRow:
    return IncomingEventRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=team_id,
        channel="dingtalk",
        sender_channel_user_id=user_id,
        sender_display_name="Tester",
        text_redacted="hello",
        reply_endpoint_json={"member_id": user_id},
    )


class FakeLlm:
    def __init__(self):
        self.calls: list[dict] = []

    def complete_with_tools(self, *, messages, tools, temperature=0.1):
        self.calls.append({"messages": messages, "tools": tools})
        return {
            "content": "done",
            "tool_calls": [],
            "raw_assistant_message": {"role": "assistant", "content": "done"},
        }


def test_memory_tool_service_search_and_expand():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    messages = [
        _msg(session_row.id, "user", "nebula cluster sizing question", offset=1),
        _msg(session_row.id, "assistant", "nebula noted", kind="final", offset=2),
        _msg(session_row.id, "user", "nebula follow up detail", offset=3),
        _msg(session_row.id, "assistant", "more nebula", kind="final", offset=4),
    ]
    db.add(session_row)
    for message in messages:
        db.add(message)
    db.commit()
    archive_and_index_session(db, session_row, index_version=1)
    archive_header = db.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    generate_session_summary(db, session_row, archive_header)
    db.commit()

    scope = resolve_search_scope(
        team_id="team-a",
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    service = MemoryToolService(
        db,
        config=AssistantChatMemoryConfig(
            recall=MemoryRecallBudgetConfig(expand_neighbor_count=1),
            features=MemoryFeatureFlags(auto_recall_per_turn=True),
        ),
        scope=scope,
        visibility_context=VisibilityContext.private("user-a"),
    )
    search_result = service.search("nebula")
    assert search_result["ok"] is True
    hits = search_result["result"]["fragments"]
    assert hits
    anchor = hits[0]["anchor"]
    expand_result = service.expand(
        session_id=anchor["session_id"],
        chunk_index=anchor["chunk_index"],
        start_seq=anchor["start_seq"],
        end_seq=anchor["end_seq"],
    )
    assert expand_result["ok"] is True
    assert expand_result["result"]["prev_hits"] or expand_result["result"]["next_hits"]

    summary_result = service.get_session_summary(session_row.id)
    assert summary_result["ok"] is True
    assert summary_result["result"]["session_id"] == session_row.id


def test_memory_tool_denies_cross_scope_session():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row(
        status="closed",
        closed_at=datetime.now(timezone.utc),
        team_id="team-b",
        user_id="user-b",
        conversation_id="user-b",
    )
    db.add(session_row)
    db.add(_msg(session_row.id, "user", "private other team", offset=1))
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
    service = MemoryToolService(
        db,
        config=AssistantChatMemoryConfig(),
        scope=scope,
        visibility_context=VisibilityContext.private("user-a"),
    )
    denied = service.get_session_summary(session_row.id)
    assert denied["ok"] is False


def test_agent_runtime_invokes_local_memory_tool():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    db.add(session_row)
    db.add(_msg(session_row.id, "user", "runtime memory query violet", offset=1))
    db.add(_msg(session_row.id, "assistant", "violet ok", kind="final", offset=2))
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
    memory_tools = MemoryToolService(
        db,
        config=AssistantChatMemoryConfig(),
        scope=scope,
        visibility_context=VisibilityContext.private("user-a"),
    )
    llm = FakeLlm()
    llm.script = [
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "memory_search",
                    "arguments": json.dumps({"query": "violet"}),
                }
            ],
            "raw_assistant_message": {},
        },
        {"content": "found it", "tool_calls": [], "raw_assistant_message": {}},
    ]
    # FakeLlm uses script list — adapt
    class ScriptLlm(FakeLlm):
        def __init__(self):
            super().__init__()
            self.script = llm.script

        def complete_with_tools(self, *, messages, tools, temperature=0.1):
            self.calls.append({"messages": messages, "tools": tools})
            if not self.script:
                return {"content": "done", "tool_calls": [], "raw_assistant_message": {}}
            return self.script.pop(0)

    runtime = AgentRuntime(
        llm=ScriptLlm(),
        executor=MagicMock(),
        capabilities=[],
        subject_id="user-a",
        memory_tools=memory_tools,
    )
    tool_names = {t["function"]["name"] for t in runtime._tools}
    assert "memory_search" in tool_names

    text = runtime.run(
        system="sys",
        history=[],
        user_text="find violet",
        actor_member_id="user-a",
        team_id="team-a",
        role="member",
    )
    assert text == "found it"


def test_orchestrator_injects_recall_into_system_prompt(monkeypatch):
    Session = init_assistant_db("sqlite://")
    db = Session()
    closed = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    open_row = _session_row()
    for row, keyword in ((closed, "injection-signal"), (open_row, "current-turn")):
        db.add(row)
        db.add(_msg(row.id, "user", f"{keyword} unique phrase", offset=1))
        db.add(_msg(row.id, "assistant", "ack", kind="final", offset=2))
    db.commit()
    archive_and_index_session(db, closed, index_version=1)
    db.commit()

    captured: dict = {}

    class CaptureLlm:
        def complete_with_tools(self, *, messages, tools, temperature=0.1):
            captured["system"] = messages[0]["content"]
            return {
                "content": "reply",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "reply"},
            }

    from assistant_platform import config as config_module

    base_config = config_module.AssistantConfig(
        llm=config_module.AssistantLlmConfig(enabled=True, api_key="k", model="m"),
        chat_memory=AssistantChatMemoryConfig(
            recall=MemoryRecallBudgetConfig(context_token_budget=800),
            features=MemoryFeatureFlags(auto_recall_per_turn=True, profile_compile=False),
        ),
    )

    monkeypatch.setattr(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        lambda _cfg: CaptureLlm(),
    )
    monkeypatch.setattr(
        "assistant_platform.conversation.orchestrator.resolve_capabilities",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "assistant_platform.conversation.orchestrator.compose_system_supplement",
        lambda *_a, **_k: "",
    )

    incoming = _incoming()
    reply = generate_reply_text(
        db,
        config=base_config,
        incoming=incoming,
        text="tell me about injection-signal",
        session_row=open_row,
    )
    assert reply == "reply"
    system = captured.get("system", "")
    assert "injection-signal" in system.lower()
    assert "历史记忆" in system or "召回" in system
