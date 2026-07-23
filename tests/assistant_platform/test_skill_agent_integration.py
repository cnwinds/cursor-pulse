from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.conversation.agent_tools import tools_from_capabilities
from assistant_platform.contracts.provider import CapabilityInvokeResult
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry


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


def test_tools_include_load_skill_docs_when_enabled():
    tools = tools_from_capabilities([_cap("quota.self.read")], include_skill_tools=True)
    names = [t["function"]["name"] for t in tools]
    assert "load_skill_docs" in names


def test_runtime_invokes_load_skill_docs_locally():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    actor = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    llm = FakeLlm(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "load_skill_docs",
                        "arguments": json.dumps(
                            {"skill_id": "cursor.self/overview", "section": "overview"}
                        ),
                    }
                ],
                "raw_assistant_message": {},
            },
            {
                "content": "好的，我来查额度",
                "tool_calls": [
                    {"id": "c2", "name": "quota_self_read", "arguments": "{}"}
                ],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "好的，我来查额度",
                    "tool_calls": [
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "quota_self_read", "arguments": "{}"},
                        }
                    ],
                },
            },
            {
                "content": "本月额度还剩不少",
                "tool_calls": [],
                "raw_assistant_message": {
                    "role": "assistant",
                    "content": "本月额度还剩不少",
                },
            },
        ]
    )
    executor = MagicMock()
    executor.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={"schema_version": 1, "accounts": []},
    )
    rt = AgentRuntime(
        llm=llm,
        executor=executor,
        capabilities=[_cap("quota.self.read")],
        max_tool_rounds=5,
        subject_id="u1",
        skill_registry=reg,
        skill_actor=actor,
    )
    text = rt.run(
        system="sys",
        history=[],
        user_text="额度多少",
        actor_member_id="m1",
        team_id="t1",
        role="member",
    )
    assert "额度" in text
    executor.invoke.assert_called_once()
    second_msgs = llm.calls[1]["messages"]
    tool_payload = next(m for m in second_msgs if m.get("role") == "tool")
    data = json.loads(tool_payload["content"])
    assert data["ok"] is True
    assert data["skill_id"] == "cursor.self/overview"
    assert "quota_self_read" in data["markdown"]
    assert data["total_lines"] > 0
    assert data["loaded_lines"] == data["total_lines"]
    assert data["has_more"] is False


def test_invoke_load_skill_docs_continuation(tmp_path: Path):
    from assistant_platform.skills.agent_tools import invoke_load_skill_docs

    docs = tmp_path / "docs"
    docs.mkdir()
    body_lines = [f"line-{i}" for i in range(1, 251)]
    (docs / "long.md").write_text(
        "---\nname: Long\naudience: [member]\nwhen_to_use:\n  - t\n---\n"
        + "\n".join(body_lines)
        + "\n",
        encoding="utf-8",
    )
    reg = SkillRegistry(root=tmp_path)
    actor = SkillActorContext("m1", "member", frozenset())
    first = json.loads(
        invoke_load_skill_docs(
            reg,
            actor,
            json.dumps({"skill_id": "long", "start_line": 1, "max_lines": 200}),
            token_budget=8000,
        )
    )
    assert first["ok"] is True
    assert first["has_more"] is True
    assert first["next_start_line"] == 201
    second = json.loads(
        invoke_load_skill_docs(
            reg,
            actor,
            json.dumps({"skill_id": "long", "start_line": 201}),
            token_budget=8000,
        )
    )
    assert second["ok"] is True
    assert second["start_line"] == 201
    assert second["has_more"] is False
    assert "line-250" in second["markdown"]
