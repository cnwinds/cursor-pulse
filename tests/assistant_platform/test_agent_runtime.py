from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.contracts.provider import CapabilityInvokeResult


@dataclass
class FakeLlm:
    script: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def complete_with_tools(self, *, messages, tools, temperature=0.1):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.script:
            return {
                "content": "done",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "done"},
            }
        return self.script.pop(0)


def _cap(key: str) -> ResolvedCapability:
    return ResolvedCapability(
        key=key,
        version="1",
        risk_level="read",
        display_name=key,
        description=key,
        input_schema={"type": "object", "properties": {}},
    )


def test_runtime_returns_content_without_tools():
    llm = FakeLlm(
        script=[
            {
                "content": "你好，我是小脉",
                "tool_calls": [],
                "raw_assistant_message": {},
            }
        ]
    )
    executor = MagicMock()
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("quota.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
    )
    text = rt.run(
        system="sys",
        history=[{"role": "user", "content": "hi"}],
        user_text="你好",
        actor_member_id="m1",
        team_id="t1",
        role="member",
    )
    assert text == "你好，我是小脉"
    executor.invoke.assert_not_called()


def test_runtime_single_tool_then_answer():
    llm = FakeLlm(
        script=[
            {
                "content": "",
                "tool_calls": [{"id": "c1", "name": "quota_self_read", "arguments": "{}"}],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "quota_self_read", "arguments": "{}"},
                        }
                    ],
                },
            },
            {
                "content": "你的额度还剩 10",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "你的额度还剩 10"},
            },
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="额度=10",
        result={"quota": 10},
    )
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
        user_text="查额度",
        actor_member_id="m1",
        team_id="t1",
        role="member",
    )
    assert text == "你的额度还剩 10"
    executor.invoke.assert_called_once()
    kwargs = executor.invoke.call_args.kwargs
    assert kwargs["capability_key"] == "quota.self.read"
    assert kwargs["confirmed"] is True


def test_runtime_multiple_tools_same_round_all_execute():
    llm = FakeLlm(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {"id": "c1", "name": "quota_self_read", "arguments": "{}"},
                    {"id": "c2", "name": "usage_self_read", "arguments": "{}"},
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "quota_self_read", "arguments": "{}"},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "usage_self_read", "arguments": "{}"},
                        },
                    ],
                },
            },
            {"content": "汇总好了", "tool_calls": [], "raw_assistant_message": {}},
        ]
    )
    executor = MagicMock()
    executor.invoke.side_effect = [
        CapabilityInvokeResult(status="succeeded", user_message="q", result={}),
        CapabilityInvokeResult(status="succeeded", user_message="u", result={}),
    ]
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("quota.self.read"), _cap("usage.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
    )
    text = rt.run(
        system="s",
        history=[],
        user_text="都看看",
        actor_member_id="m1",
        team_id="t1",
        role="member",
    )
    assert text == "汇总好了"
    assert executor.invoke.call_count == 2


def test_runtime_hits_max_rounds():
    forever = {
        "content": "",
        "tool_calls": [{"id": "c1", "name": "quota_self_read", "arguments": "{}"}],
        "raw_assistant_message": {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "quota_self_read", "arguments": "{}"},
                }
            ],
        },
    }
    llm = FakeLlm(script=[forever, forever, forever])
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded", user_message="ok", result={}
    )
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("quota.self.read")],
        max_tool_rounds=2,
        subject_id="u1",
    )
    text = rt.run(
        system="s",
        history=[],
        user_text="循环",
        actor_member_id="m1",
        team_id="t1",
        role="member",
    )
    assert "拆" in text or "步骤" in text


def test_runtime_private_loan_self_read_uses_llm_not_verbatim():
    """key.loan.self.read 已退出 verbatim：须再走一轮 LLM 按 result 排版。"""
    full_key = "crsr_bot_loan_key_plaintext_full_value_here"
    llm = FakeLlm(
        script=[
            {
                "content": "好的，我来查一下你当前借用的 Key 状态～",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "key_loan_self_read",
                        "arguments": '{"text": "查看我借用的key"}',
                    }
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "好的，我来查一下你当前借用的 Key 状态～",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "key_loan_self_read",
                                "arguments": '{"text": "查看我借用的key"}',
                            },
                        }
                    ],
                },
            },
            {
                "content": f"当前借用 Key：{full_key}",
                "tool_calls": [],
                "raw_assistant_message": {},
            },
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "empty_reason": None,
            "loan": {
                "lender_name": "Admin",
                "api_key": full_key,
                "approx_borrowed_usd": 1.0,
            },
        },
    )
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("key.loan.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
    )
    text = rt.run(
        system="sys",
        history=[],
        user_text="查看我借用的key",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        conversation_type="private",
    )
    assert full_key in text
    assert len(llm.calls) == 2


def test_runtime_group_does_not_passthrough_key_reply():
    full_key = "crsr_bot_loan_key_plaintext_full_value_here"
    llm = FakeLlm(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "key_loan_self_read",
                        "arguments": "{}",
                    }
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "key_loan_self_read",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            },
            {
                "content": "Key：crsr_cd96...28df",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "Key：crsr_cd96...28df"},
            },
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message=f"Key：{full_key}",
        result={},
    )
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("key.loan.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
    )
    text = rt.run(
        system="sys",
        history=[],
        user_text="查看我借用的key",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        conversation_type="group",
    )
    assert text == "Key：crsr_cd96...28df"
    assert len(llm.calls) == 2
