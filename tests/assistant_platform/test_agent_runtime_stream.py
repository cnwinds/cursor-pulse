from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from tests.assistant_platform.test_agent_runtime import _cap

from assistant_platform.contracts.provider import CapabilityInvokeResult
from assistant_platform.conversation.agent_runtime import AgentRuntime, AgentUnavailable


@dataclass
class StreamingFakeLlm:
    script: list[dict] = field(default_factory=list)
    streamed: list[bool] = field(default_factory=list)

    def complete_with_tools(self, *, messages, tools, temperature=0.1, on_content_delta=None):
        self.streamed.append(on_content_delta is not None)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            if on_content_delta is not None:
                on_content_delta("半截")
            raise step
        content = step.get("content") or ""
        if on_content_delta is not None and content:
            for i in range(1, len(content) + 1):
                on_content_delta(content[:i])
        raw = {"role": "assistant", "content": content or None}
        if step.get("tool_calls"):
            raw["tool_calls"] = [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in step["tool_calls"]
            ]
        return {**step, "raw_assistant_message": raw}


@dataclass
class RecordingSink:
    events: list[tuple[str, str]] = field(default_factory=list)

    def update(self, text: str) -> None:
        self.events.append(("update", text))

    def discard(self) -> None:
        self.events.append(("discard", ""))


def _runtime(llm, executor=None):
    if executor is None:
        executor = MagicMock()
        executor.invoke.return_value = CapabilityInvokeResult(
            status="succeeded",
            user_message="",
            result={"schema_version": 1, "accounts": []},
        )
    return AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("usage.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
    )


def _run(rt, **kwargs):
    return rt.run(
        system="sys",
        history=[],
        user_text="我的用量",
        actor_member_id="m1",
        team_id="t1",
        role="member",
        **kwargs,
    )


def test_runtime_streams_final_content_into_sink_without_discard():
    llm = StreamingFakeLlm(script=[{"content": "你好", "tool_calls": []}])
    sink = RecordingSink()
    text = _run(_runtime(llm), stream_sink=sink)
    assert text == "你好"
    assert llm.streamed == [True]
    assert sink.events == [("update", "你"), ("update", "你好")]


def test_runtime_discards_draft_after_tool_round_interim():
    llm = StreamingFakeLlm(
        script=[
            {"content": "好的", "tool_calls": [{"id": "c1", "name": "usage_self_read", "arguments": "{}"}]},
            {"content": "表", "tool_calls": []},
        ]
    )
    sink = RecordingSink()
    order: list[str] = []
    text = _run(
        _runtime(llm),
        stream_sink=sink,
        on_interim_reply=lambda t: (order.append(f"interim:{t}"), sink.events.append(("interim", t))),
    )
    assert text == "表"
    assert sink.events == [
        ("update", "好"),
        ("update", "好的"),
        ("interim", "好的"),
        ("discard", ""),
        ("update", "表"),
    ]


def test_runtime_discards_draft_when_llm_fails():
    llm = StreamingFakeLlm(script=[RuntimeError("boom")])
    sink = RecordingSink()
    try:
        _run(_runtime(llm), stream_sink=sink)
    except AgentUnavailable:
        pass
    else:
        raise AssertionError("expected AgentUnavailable")
    assert sink.events == [("update", "半截"), ("discard", "")]


def test_runtime_without_sink_does_not_request_streaming():
    llm = StreamingFakeLlm(script=[{"content": "你好", "tool_calls": []}])
    assert _run(_runtime(llm)) == "你好"
    assert llm.streamed == [False]
