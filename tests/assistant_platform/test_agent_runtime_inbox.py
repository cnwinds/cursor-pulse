from __future__ import annotations

import json
from unittest.mock import MagicMock

from assistant_platform.capabilities.executor import CapabilityExecutor
from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.conversation.turn_inbox import InboxEntry


def _runtime(*, llm: MagicMock, caps: list | None = None) -> AgentRuntime:
    caps = caps or []
    executor = MagicMock(spec=CapabilityExecutor)
    return AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=caps,
        max_tool_rounds=5,
        subject_id="subject-1",
    )


def test_agent_injects_inbox_before_second_llm_round():
    llm = MagicMock()
    llm.complete_with_tools.side_effect = [
        {
            "content": "",
            "tool_calls": [
                {"id": "c1", "name": "usage_query", "arguments": '{"period":"2026-07"}'}
            ],
            "raw_assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "usage_query",
                            "arguments": '{"period":"2026-07"}',
                        },
                    }
                ],
            },
        },
        {
            "content": "6月用量如下",
            "tool_calls": [],
            "raw_assistant_message": {"role": "assistant", "content": "6月用量如下"},
        },
    ]

    usage_cap = ResolvedCapability(
        key="usage.query",
        version="1",
        risk_level="read",
        display_name="查询用量",
        description="查询用量",
        confirmation_required=False,
    )
    runtime = _runtime(llm=llm, caps=[usage_cap])
    runtime._executor.invoke.return_value = MagicMock(
        status="succeeded",
        user_message="ok",
        result={},
    )

    inbox = MagicMock()
    inbox.drain_unconsumed.side_effect = [
        [],
        [InboxEntry(message_id="m2", text="查6月份的", received_at="")],
        [],
    ]

    reply = runtime.run(
        system="sys",
        history=[],
        user_text="查询用量",
        actor_member_id="m1",
        team_id="team-1",
        role="member",
        inbox=inbox,
    )

    assert reply == "6月用量如下"
    assert llm.complete_with_tools.call_count == 2
    second_messages = llm.complete_with_tools.call_args_list[1].kwargs["messages"]
    user_contents = [
        m["content"] for m in second_messages if m.get("role") == "user"
    ]
    assert "查询用量" in user_contents
    assert "查6月份的" in user_contents
    inbox.mark_consumed.assert_called_once_with("m2")


def test_agent_injects_inbox_after_each_tool():
    llm = MagicMock()
    llm.complete_with_tools.side_effect = [
        {
            "content": "",
            "tool_calls": [
                {"id": "c1", "name": "usage_query", "arguments": "{}"},
                {"id": "c2", "name": "quota_self_read", "arguments": "{}"},
            ],
            "raw_assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "usage_query", "arguments": "{}"},
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "quota_self_read", "arguments": "{}"},
                    },
                ],
            },
        },
        {
            "content": "完成",
            "tool_calls": [],
            "raw_assistant_message": {"role": "assistant", "content": "完成"},
        },
    ]

    caps = [
        ResolvedCapability(
            key="usage.query",
            version="1",
            risk_level="read",
            display_name="查询用量",
            description="",
            confirmation_required=False,
        ),
        ResolvedCapability(
            key="quota.self.read",
            version="1",
            risk_level="read",
            display_name="查额度",
            description="",
            confirmation_required=False,
        ),
    ]
    runtime = _runtime(llm=llm, caps=caps)
    runtime._executor.invoke.return_value = MagicMock(
        status="succeeded",
        user_message="ok",
        result={},
    )

    inbox = MagicMock()
    inbox.drain_unconsumed.side_effect = [
        [],
        [InboxEntry(message_id="m2", text="只要6月", received_at="")],
        [],
        [],
    ]

    runtime.run(
        system="sys",
        history=[],
        user_text="查一下",
        actor_member_id="m1",
        team_id="team-1",
        role="member",
        inbox=inbox,
    )

    second_messages = llm.complete_with_tools.call_args_list[1].kwargs["messages"]
    serialized = json.dumps(second_messages, ensure_ascii=False)
    assert "只要6月" in serialized
