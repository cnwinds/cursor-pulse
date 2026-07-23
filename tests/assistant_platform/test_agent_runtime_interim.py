from __future__ import annotations

from unittest.mock import MagicMock

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.conversation.agent_tools import NOTIFY_USER_TOOL_NAME
from assistant_platform.contracts.provider import CapabilityInvokeResult
from tests.assistant_platform.test_agent_runtime import FakeLlm, _cap


def test_runtime_nudges_when_ack_without_tool_calls():
    llm = FakeLlm(
        script=[
            {
                "content": "好的，来看看你的额度情况～",
                "tool_calls": [],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "好的，来看看你的额度情况～",
                },
            },
            {
                "content": "稍等",
                "tool_calls": [
                    {"id": "c1", "name": "quota_self_read", "arguments": "{}"}
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "稍等",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "quota_self_read",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            },
            {
                "content": "本月已用 28%",
                "tool_calls": [],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "本月已用 28%",
                },
            },
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={"schema_version": 1, "accounts": [{"total_pct": 28.5}]},
    )
    interim: list[str] = []
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("quota.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
    )
    text = rt.run(
        system="sys",
        history=[],
        user_text="我的额度",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        on_interim_reply=interim.append,
    )
    assert text == "本月已用 28%"
    assert interim[0] == "好的，来看看你的额度情况～"
    assert any(
        (m.get("role") == "user" and "没有调用任何 tool" in (m.get("content") or ""))
        for call in llm.calls
        for m in call["messages"]
    )
    executor.invoke.assert_called_once()

    llm = FakeLlm(
        script=[
            {
                "content": "好的，我先查一下，请稍等",
                "tool_calls": [
                    {"id": "c1", "name": "usage_query", "arguments": "{}"}
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "好的，我先查一下，请稍等",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "usage_query", "arguments": "{}"},
                        }
                    ],
                },
            },
            {"content": "查完了", "tool_calls": [], "raw_assistant_message": {}},
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded", user_message="ok", result={}
    )
    interim: list[str] = []
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("usage.query")],
        max_tool_rounds=5,
        subject_id="u1",
    )
    text = rt.run(
        system="s",
        history=[],
        user_text="查用量",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        on_interim_reply=interim.append,
    )
    assert text == "查完了"
    assert interim == ["好的，我先查一下，请稍等"]


def test_runtime_notify_user_tool_emits_interim():
    llm = FakeLlm(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": NOTIFY_USER_TOOL_NAME,
                        "arguments": '{"message":"已经查到数据了，再稍等下就好了"}',
                    }
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": NOTIFY_USER_TOOL_NAME,
                                "arguments": '{"message":"已经查到数据了，再稍等下就好了"}',
                            },
                        }
                    ],
                },
            },
            {
                "content": "",
                "tool_calls": [
                    {"id": "c2", "name": "usage_query", "arguments": "{}"}
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "usage_query", "arguments": "{}"},
                        }
                    ],
                },
            },
            {"content": "6月用量如下", "tool_calls": [], "raw_assistant_message": {}},
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded", user_message="ok", result={}
    )
    interim: list[str] = []
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("usage.query")],
        max_tool_rounds=5,
        subject_id="u1",
        max_interim_replies=3,
    )
    text = rt.run(
        system="s",
        history=[],
        user_text="查用量",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        on_interim_reply=interim.append,
    )
    assert text == "6月用量如下"
    assert interim == ["已经查到数据了，再稍等下就好了"]
    executor.invoke.assert_called_once()


def test_runtime_caps_interim_replies():
    llm = FakeLlm(
        script=[
            {
                "content": "进度1",
                "tool_calls": [{"id": "c1", "name": "usage_query", "arguments": "{}"}],
                "raw_assistant_message": {},
            },
            {
                "content": "进度2",
                "tool_calls": [{"id": "c2", "name": "usage_query", "arguments": "{}"}],
                "raw_assistant_message": {},
            },
            {"content": "完成", "tool_calls": [], "raw_assistant_message": {}},
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded", user_message="ok", result={}
    )
    interim: list[str] = []
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("usage.query")],
        max_tool_rounds=5,
        subject_id="u1",
        max_interim_replies=1,
    )
    rt.run(
        system="s",
        history=[],
        user_text="查",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        on_interim_reply=interim.append,
    )
    assert interim == ["进度1"]
