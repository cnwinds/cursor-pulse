from __future__ import annotations

import json
from datetime import datetime, timezone

from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.conversation.agent_trace import (
    persist_agent_trace_event,
    redact_tool_arguments,
    redact_tool_result,
)
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_history import load_session_history_messages
from assistant_platform.storage.db import init_assistant_db


def test_redact_tool_arguments_strips_api_key_and_masks_cursor_key():
    raw = json.dumps(
        {
            "text": "绑定",
            "api_key": "crsr_should_not_appear_in_ledger_abcdefgh",
            "note": "use crsr_ABCDEFGHIJKLMNOPQRSTUVWX",
        }
    )
    redacted = redact_tool_arguments(raw)
    dumped = json.dumps(redacted, ensure_ascii=False)
    assert "api_key" not in redacted if isinstance(redacted, dict) else "api_key" not in dumped
    assert "crsr_should_not_appear" not in dumped
    assert "ABCDEFGHIJKLMNOPQRSTUVWX" not in dumped


def test_redact_tool_result_masks_cursor_key():
    text = redact_tool_result('{"user_message":"key=crsr_ABCDEFGHIJKLMNOPQRSTUVWX"}')
    assert "ABCDEFGHIJKLMNOPQRSTUVWX" not in text


def test_persist_tool_and_thinking_events():
    Session = init_assistant_db("sqlite://")
    db = Session()
    session_row = ChatSessionRow(
        assistant_id="xiaomai",
        team_id="team-1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="open",
    )
    db.add(session_row)
    db.commit()

    thinking = persist_agent_trace_event(
        db,
        session_row=session_row,
        event={
            "type": "thinking",
            "text": "我先查一下用量",
            "round": 1,
            "delivered_as_interim": False,
        },
    )
    tool = persist_agent_trace_event(
        db,
        session_row=session_row,
        event={
            "type": "tool",
            "round": 1,
            "tool_call_id": "call_1",
            "name": "usage_self_read",
            "arguments": json.dumps({"text": "我的用量", "api_key": "secret"}),
            "result": json.dumps(
                {"ok": True, "result": {"schema_version": 1, "accounts": []}},
                ensure_ascii=False,
            ),
        },
    )
    skipped = persist_agent_trace_event(
        db,
        session_row=session_row,
        event={
            "type": "thinking",
            "text": "已发过进度",
            "round": 1,
            "delivered_as_interim": True,
        },
    )
    context = persist_agent_trace_event(
        db,
        session_row=session_row,
        event={
            "type": "context",
            "skills": [
                {
                    "skill_id": "cursor.self/tasks/my-usage",
                    "name": "我的用量",
                    "summary": "查个人用量",
                }
            ],
            "tools": [
                {
                    "name": "usage_self_read",
                    "capability_key": "usage.self.read",
                    "display_name": "我的用量",
                },
                {
                    "name": "notify_user",
                    "capability_key": "",
                    "display_name": "进度通知",
                },
            ],
        },
    )
    assert thinking is not None
    assert thinking.role == "assistant"
    assert thinking.meta_json.get("kind") == "thinking"
    assert tool is not None
    assert tool.role == "tool"
    assert tool.meta_json.get("name") == "usage_self_read"
    assert "api_key" not in json.dumps(tool.meta_json.get("arguments"))
    assert "schema_version" in (tool.text_redacted or "")
    assert skipped is None
    assert context is not None
    assert context.role == "assistant"
    assert context.meta_json.get("kind") == "context"
    assert context.meta_json.get("ledger_only") is True
    assert "技能 1 · 工具 2" in (context.text_redacted or "")
    assert len(context.meta_json.get("skills") or []) == 1
    assert len(context.meta_json.get("tools") or []) == 2

    db.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="user",
            text_redacted="我的用量",
            meta_json={},
        )
    )
    db.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="最终回复",
            meta_json={"kind": "final"},
        )
    )
    db.commit()
    history = load_session_history_messages(db, session_id=session_row.id, limit=20)
    assert all(item["role"] in {"user", "assistant"} for item in history)
    assert all("我先查一下用量" not in item["content"] for item in history)
    assert all("技能 1 · 工具 2" not in item["content"] for item in history)
    assert any(item["content"] == "最终回复" for item in history)
    db.close()

class _ScriptLlm:
    def __init__(self, script: list[dict]):
        self.script = list(script)

    def complete_with_tools(self, *, messages, tools, temperature=0.1):
        if not self.script:
            return {"content": "done", "tool_calls": [], "raw_assistant_message": {}}
        return self.script.pop(0)


def test_agent_runtime_emits_tool_trace_with_args_and_result():
    traces: list[dict] = []

    class _Exec:
        def invoke(self, **kwargs):
            from assistant_platform.contracts.provider import CapabilityInvokeResult

            return CapabilityInvokeResult(
                status="succeeded",
                user_message="ok",
                result={"hello": 1},
            )

    from assistant_platform.capabilities.resolve import ResolvedCapability

    caps = [
        ResolvedCapability(
            key="usage.self.read",
            version="1",
            risk_level="read",
            display_name="我的用量",
            description="d",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    runtime = AgentRuntime(
        llm=_ScriptLlm(
            [
                {
                    "content": "稍等我查一下",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "name": "usage_self_read",
                            "arguments": json.dumps({"text": "我的用量"}),
                        }
                    ],
                    "raw_assistant_message": {},
                },
                {
                    "content": "查完了",
                    "tool_calls": [],
                    "raw_assistant_message": {},
                },
            ]
        ),
        executor=_Exec(),
        capabilities=caps,
        subject_id="u1",
        max_interim_replies=0,
    )
    text = runtime.run(
        system="sys",
        history=[],
        user_text="我的用量",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        on_agent_trace=traces.append,
    )
    assert text == "查完了"
    assert any(t.get("type") == "thinking" for t in traces)
    tool_events = [t for t in traces if t.get("type") == "tool"]
    assert len(tool_events) == 1
    assert tool_events[0]["name"] == "usage_self_read"
    args = json.loads(tool_events[0]["arguments"])
    assert args.get("text") == "我的用量"
    assert "hello" in tool_events[0]["result"]
