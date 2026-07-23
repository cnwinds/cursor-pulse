# DingTalk LLM Agent + Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace DingTalk text orchestration (rule intents + JSON LLM classification + pending confirm) with an LLM Agent that calls capability tools in a multi-round loop and replies in natural language.

**Architecture:** Add `AgentRuntime` under `assistant_platform/conversation/`. Map role-filtered capabilities to OpenAI tools, load only the current open session history, inject Prompt Studio fragments + agent policy into system, loop `complete_with_tools` (multi tool_calls per round, max 20). Keep `CapabilityExecutor` as the sole execution surface. Enforce per-user isolation via session id / subject_id.

**Tech Stack:** Python 3, SQLAlchemy, httpx OpenAI-compatible chat completions (tools), pytest, existing Assistant Platform + Pulse capability invoke.

**Spec:** `docs/superpowers/specs/2026-07-16-dingtalk-llm-agent-tools-design.md`

---

## File map

| Path | Responsibility |
|------|----------------|
| `assistant_platform/conversation/agent_tools.py` | Capability → OpenAI tools; name encode/decode; exclude `bot.help` |
| `assistant_platform/conversation/subject.py` | Resolve stable `subject_id` for isolation / future memory |
| `assistant_platform/conversation/session_history.py` | Load user/assistant texts for one `session_id` only |
| `assistant_platform/conversation/agent_policy.py` | Fixed Agent system policy string builder |
| `assistant_platform/conversation/agent_runtime.py` | Tool loop, invoke tools, round limits |
| `assistant_platform/llm/client.py` | `complete_with_tools(messages=..., tools=...)` with `tool_call_id` |
| `assistant_platform/config.py` | `agent_max_tool_rounds`, `agent_history_max_messages`, `agent_total_timeout_seconds` |
| `assistant_platform/conversation/orchestrator.py` | `generate_reply_text` → Agent only; memory/simple fallback |
| `assistant_platform/prompts/seed.py` | Update default precepts for tools (not fixed commands) |
| `docs/bot-commands.md` | NL-first usage docs |
| `tests/assistant_platform/test_agent_*.py` | Unit/integration tests |

**Parallelization lock-in:** v1 invokes tools **sequentially** on the shared SQLAlchemy session (thread-unsafe). Spec still allows multi tool_calls per round; all of them run, just not in OS threads until per-call sessions exist.

---

### Task 1: Config knobs for Agent

**Files:**
- Modify: `assistant_platform/config.py`
- Test: `tests/assistant_platform/test_agent_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/assistant_platform/test_agent_config.py
from assistant_platform.config import AssistantConfig, AssistantLlmConfig


def test_agent_defaults_on_llm_config():
    llm = AssistantLlmConfig()
    assert llm.agent_max_tool_rounds == 20
    assert llm.agent_history_max_messages == 40
    assert llm.agent_total_timeout_seconds == 120.0


def test_assistant_config_embeds_agent_defaults():
    cfg = AssistantConfig()
    assert cfg.llm.agent_max_tool_rounds == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_agent_config.py -v`  
Expected: FAIL with AttributeError / missing fields

- [ ] **Step 3: Write minimal implementation**

In `assistant_platform/config.py`, extend `AssistantLlmConfig`:

```python
class AssistantLlmConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    intent_min_confidence: float = 0.6
    timeout_seconds: float = 30.0
    agent_max_tool_rounds: int = 20
    agent_history_max_messages: int = 40
    agent_total_timeout_seconds: float = 120.0
```

In `load_assistant_config()`, when building `AssistantLlmConfig`, also read env:

```python
agent_max_tool_rounds=int(os.environ.get("ASSISTANT_AGENT_MAX_TOOL_ROUNDS", "20")),
agent_history_max_messages=int(os.environ.get("ASSISTANT_AGENT_HISTORY_MAX_MESSAGES", "40")),
agent_total_timeout_seconds=float(os.environ.get("ASSISTANT_AGENT_TOTAL_TIMEOUT_SECONDS", "120")),
```

In `_apply_team_assistant_llm_overrides`, extend the override key loop to include the three `agent_*` keys.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/assistant_platform/test_agent_config.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/config.py tests/assistant_platform/test_agent_config.py
git commit -m "feat: add assistant agent tool-loop config knobs"
```

---

### Task 2: Capability → OpenAI tools mapping

**Files:**
- Create: `assistant_platform/conversation/agent_tools.py`
- Test: `tests/assistant_platform/test_agent_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/assistant_platform/test_agent_tools.py
from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_tools import (
    TOOL_EXCLUSIONS,
    tools_from_capabilities,
    tool_name_for_capability,
    resolve_capability_for_tool_name,
)


def test_tool_name_roundtrip_via_resolve():
    cap = ResolvedCapability(
        key="quota.self.read",
        version="1",
        risk_level="read",
        display_name="查询本人额度",
        description="读取额度快照",
        input_schema={"type": "object", "properties": {"period": {"type": "string"}}},
    )
    assert tool_name_for_capability(cap.key) == "quota_self_read"
    assert resolve_capability_for_tool_name("quota_self_read", [cap]) is cap


def test_bot_help_excluded():
    caps = [
        ResolvedCapability(
            key="bot.help",
            version="1",
            risk_level="read",
            display_name="帮助",
            description="帮助",
            input_schema={"type": "object", "properties": {}},
        ),
        ResolvedCapability(
            key="quota.self.read",
            version="1",
            risk_level="read",
            display_name="查询本人额度",
            description="读取额度快照",
            input_schema={
                "type": "object",
                "properties": {"period": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
    ]
    tools = tools_from_capabilities(caps)
    names = [t["function"]["name"] for t in tools]
    assert "bot_help" not in names
    assert "quota_self_read" in names
    assert "bot.help" in TOOL_EXCLUSIONS
    quota = next(t for t in tools if t["function"]["name"] == "quota_self_read")
    assert quota["type"] == "function"
    assert "额度" in quota["function"]["description"]
    assert quota["function"]["parameters"]["properties"]["period"]["type"] == "string"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_agent_tools.py -v`  
Expected: FAIL import error

- [ ] **Step 3: Write minimal implementation**

```python
# assistant_platform/conversation/agent_tools.py
from __future__ import annotations

from typing import Any, Iterable

from assistant_platform.capabilities.resolve import ResolvedCapability

TOOL_EXCLUSIONS = frozenset({"bot.help"})


def tool_name_for_capability(capability_key: str) -> str:
    return capability_key.replace(".", "_")


def tools_from_capabilities(capabilities: Iterable[ResolvedCapability]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for cap in capabilities:
        if cap.key in TOOL_EXCLUSIONS:
            continue
        schema = cap.input_schema or {"type": "object", "properties": {}}
        description = f"{cap.display_name}。{cap.description}".strip()
        if cap.risk_level in ("sensitive", "destructive") or cap.confirmation_required:
            description += (
                " 【高风险】调用前必须先向用户说明将执行的操作并获得明确同意。"
            )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name_for_capability(cap.key),
                    "description": description,
                    "parameters": schema,
                },
            }
        )
    return tools


def resolve_capability_for_tool_name(
    tool_name: str,
    capabilities: Iterable[ResolvedCapability],
) -> ResolvedCapability | None:
    by_name = {tool_name_for_capability(c.key): c for c in capabilities}
    return by_name.get(tool_name)
```

Always use `resolve_capability_for_tool_name` at invoke time (do not reverse `_` → `.` blindly).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/assistant_platform/test_agent_tools.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/conversation/agent_tools.py tests/assistant_platform/test_agent_tools.py
git commit -m "feat: map capabilities to OpenAI tools for agent"
```

---

### Task 3: Subject id + session history loader (isolation)

**Files:**
- Create: `assistant_platform/conversation/subject.py`
- Create: `assistant_platform/conversation/session_history.py`
- Test: `tests/assistant_platform/test_session_history.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/assistant_platform/test_session_history.py
import uuid
from datetime import datetime, timezone

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_history import load_session_history_messages
from assistant_platform.conversation.subject import resolve_subject_id
from assistant_platform.storage.db import init_assistant_db

TEAM = "team-hist"


def test_resolve_subject_id_prefers_member_id():
    assert resolve_subject_id(member_id="m1", channel_user_id="u1") == "m1"
    assert resolve_subject_id(member_id=None, channel_user_id="u1") == "u1"
    assert resolve_subject_id(member_id="", channel_user_id="u1") == "u1"


def test_history_only_loads_named_session_user_assistant():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    s_a = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u-a",
        user_id="u-a",
        status="open",
        last_activity_at=datetime.now(timezone.utc),
    )
    s_b = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u-b",
        user_id="u-b",
        status="open",
        last_activity_at=datetime.now(timezone.utc),
    )
    db.add_all([s_a, s_b])
    db.add_all(
        [
            ChatMessageRow(
                session_id=s_a.id,
                role="user",
                text_redacted="A问",
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_a.id,
                role="assistant",
                text_redacted="A答",
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_b.id,
                role="user",
                text_redacted="B机密",
                secret_refs_json=[],
                meta_json={},
            ),
        ]
    )
    db.commit()

    messages = load_session_history_messages(db, session_id=s_a.id, limit=40)
    texts = [m["content"] for m in messages]
    assert texts == ["A问", "A答"]
    assert "B机密" not in texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_session_history.py -v`  
Expected: FAIL import error

- [ ] **Step 3: Write minimal implementation**

```python
# assistant_platform/conversation/subject.py
from __future__ import annotations


def resolve_subject_id(*, member_id: str | None, channel_user_id: str | None) -> str:
    mid = (member_id or "").strip()
    if mid:
        return mid
    return (channel_user_id or "").strip()
```

```python
# assistant_platform/conversation/session_history.py
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow

_HISTORY_ROLES = frozenset({"user", "assistant"})


def load_session_history_messages(
    db_session: Session,
    *,
    session_id: str,
    limit: int,
    exclude_message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load texts for ONE session only (caller must pass the actor's open session)."""
    stmt = (
        select(ChatMessageRow)
        .where(
            ChatMessageRow.session_id == session_id,
            ChatMessageRow.role.in_(tuple(_HISTORY_ROLES)),
        )
        .order_by(ChatMessageRow.created_at.asc())
    )
    rows = list(db_session.scalars(stmt))
    if exclude_message_id:
        rows = [r for r in rows if r.id != exclude_message_id]
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return [
        {"role": row.role, "content": row.text_redacted or ""}
        for row in rows
        if (row.text_redacted or "").strip()
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/assistant_platform/test_session_history.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/conversation/subject.py assistant_platform/conversation/session_history.py tests/assistant_platform/test_session_history.py
git commit -m "feat: load per-session history with subject_id helper"
```

---

### Task 4: AssistantLlmClient.complete_with_tools (multi-turn messages)

**Files:**
- Modify: `assistant_platform/llm/client.py`
- Test: `tests/assistant_platform/test_assistant_llm_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/assistant_platform/test_assistant_llm_tools.py
import json
from unittest.mock import MagicMock, patch

from assistant_platform.llm.client import AssistantLlmClient


def test_complete_with_tools_sends_messages_and_parses_tool_calls():
    client = AssistantLlmClient(api_key="k", model="m", base_url="https://example.test/v1")
    payload_out = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "quota_self_read",
                                "arguments": "{\"period\":\"month\"}",
                            },
                        }
                    ],
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = payload_out

    with patch("assistant_platform.llm.client.httpx.Client") as Client:
        Client.return_value.__enter__.return_value.post.return_value = mock_resp
        result = client.complete_with_tools(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "查额度"},
            ],
            tools=[{"type": "function", "function": {"name": "quota_self_read", "parameters": {}}}],
        )

    assert result["content"] == ""
    assert result["tool_calls"][0]["id"] == "call_1"
    assert result["tool_calls"][0]["name"] == "quota_self_read"
    assert json.loads(result["tool_calls"][0]["arguments"]) == {"period": "month"}
    posted = Client.return_value.__enter__.return_value.post.call_args
    body = posted.kwargs["json"]
    assert body["messages"][0]["role"] == "system"
    assert body["tool_choice"] == "auto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_assistant_llm_tools.py -v`  
Expected: FAIL `complete_with_tools` missing

- [ ] **Step 3: Write minimal implementation**

Add to `AssistantLlmClient` in `assistant_platform/llm/client.py`:

```python
def complete_with_tools(
    self,
    *,
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.1,
) -> dict:
    url = f"{self.base_url}/chat/completions"
    payload = {
        "model": self.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {self.api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=self.timeout_seconds) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    message = data["choices"][0]["message"]
    tool_calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        tool_calls.append(
            {
                "id": call.get("id") or "",
                "name": fn.get("name"),
                "arguments": fn.get("arguments") or "{}",
            }
        )
    return {
        "content": (message.get("content") or "").strip(),
        "tool_calls": tool_calls,
        "raw_assistant_message": message,
    }
```

Keep existing `complete(...)` unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/assistant_platform/test_assistant_llm_tools.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/llm/client.py tests/assistant_platform/test_assistant_llm_tools.py
git commit -m "feat: add multi-turn complete_with_tools on Assistant LLM client"
```

---

### Task 5: Agent policy + AgentRuntime

**Files:**
- Create: `assistant_platform/conversation/agent_policy.py`
- Create: `assistant_platform/conversation/agent_runtime.py`
- Test: `tests/assistant_platform/test_agent_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/assistant_platform/test_agent_runtime.py
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
```

Verify `CapabilityInvokeResult` constructor against `assistant_platform/contracts/provider.py` and adjust kwargs if needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_agent_runtime.py -v`  
Expected: FAIL import error

- [ ] **Step 3: Write minimal implementation**

`assistant_platform/conversation/agent_policy.py`:

```python
from __future__ import annotations

from typing import Iterable

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_tools import TOOL_EXCLUSIONS


def build_agent_system(
    *,
    prompt_studio_supplement: str,
    capabilities: Iterable[ResolvedCapability],
    subject_id: str,
) -> str:
    caps = [c for c in capabilities if c.key not in TOOL_EXCLUSIONS]
    lines = [
        "你是团队助手，通过 function tools 完成用户请求。",
        f"当前用户 subject_id={subject_id}。不得使用或编造其他用户的数据与记忆。",
        "规则：",
        "1. 需要查数或执行操作时调用对应 tool；不要假装已经执行。",
        "2. 用户询问帮助/能做什么时：根据当前可用 tools 的名称与描述总结，不要编造未提供的能力。",
        "3. 标记为高风险的 tool：先用自然语言说明将执行的内容，等用户明确同意后再调用。",
        "4. 简单本人用量/额度优先 usage_self_read / quota_self_read；复杂分析再用 usage_query。",
        "5. 不要泄露 API Key、密钥或他人隐私。",
        "6. 用简洁友好的中文回复。",
        "",
        "当前可用能力（display_name）：",
    ]
    for c in caps:
        lines.append(
            f"- {c.display_name}（tool={c.key.replace('.', '_')}）：{c.description}"
        )
    policy = "\n".join(lines)
    supplement = (prompt_studio_supplement or "").strip()
    if supplement:
        return policy + "\n\n" + supplement
    return policy
```

`assistant_platform/conversation/agent_runtime.py`:

```python
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from assistant_platform.capabilities.executor import CapabilityExecutor
from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_tools import (
    resolve_capability_for_tool_name,
    tools_from_capabilities,
)

logger = logging.getLogger(__name__)

_UNAVAILABLE = "助手暂时不可用，请稍后再试。"
_MAX_ROUNDS_MSG = "这次需要的步骤较多，请把请求拆成更小的几步，我再继续帮你。"


class SupportsCompleteWithTools(Protocol):
    def complete_with_tools(
        self, *, messages: list[dict], tools: list[dict], temperature: float = 0.1
    ) -> dict: ...


class AgentUnavailable(Exception):
    pass


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: SupportsCompleteWithTools,
        executor: CapabilityExecutor,
        capabilities: list[ResolvedCapability],
        max_tool_rounds: int = 20,
        subject_id: str,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._capabilities = list(capabilities)
        self._max_tool_rounds = max(1, max_tool_rounds)
        self._subject_id = subject_id
        self._tools = tools_from_capabilities(self._capabilities)

    def run(
        self,
        *,
        system: str,
        history: list[dict[str, Any]],
        user_text: str,
        actor_member_id: str,
        team_id: str,
        role: str | None,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        for _round in range(self._max_tool_rounds):
            try:
                resp = self._llm.complete_with_tools(
                    messages=messages, tools=self._tools
                )
            except Exception as exc:
                logger.exception("agent llm call failed subject=%s", self._subject_id)
                raise AgentUnavailable(_UNAVAILABLE) from exc

            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                content = (resp.get("content") or "").strip()
                return content or _UNAVAILABLE

            raw_assistant = resp.get("raw_assistant_message") or {
                "role": "assistant",
                "content": resp.get("content") or None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc.get("arguments") or "{}",
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            }
            messages.append(raw_assistant)

            for tc in tool_calls:
                payload = self._invoke_one(
                    tc,
                    actor_member_id=actor_member_id,
                    team_id=team_id,
                    role=role,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "",
                        "content": payload,
                    }
                )

        return _MAX_ROUNDS_MSG

    def _invoke_one(
        self,
        tc: dict,
        *,
        actor_member_id: str,
        team_id: str,
        role: str | None,
    ) -> str:
        name = tc.get("name") or ""
        cap = resolve_capability_for_tool_name(name, self._capabilities)
        if cap is None:
            return json.dumps(
                {"ok": False, "error": f"unknown or unauthorized tool: {name}"},
                ensure_ascii=False,
            )
        try:
            args = json.loads(tc.get("arguments") or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            return json.dumps(
                {"ok": False, "error": "invalid JSON arguments"},
                ensure_ascii=False,
            )
        try:
            result = self._executor.invoke(
                actor_member_id=actor_member_id,
                team_id=team_id,
                role=role,
                capability_key=cap.key,
                arguments=args,
                confirmed=True,
                capability_version=cap.version,
            )
            return json.dumps(
                {
                    "ok": result.status == "succeeded",
                    "status": result.status,
                    "user_message": result.user_message,
                    "result": result.result,
                },
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            logger.exception(
                "tool invoke failed name=%s subject=%s", name, self._subject_id
            )
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/assistant_platform/test_agent_runtime.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/conversation/agent_policy.py assistant_platform/conversation/agent_runtime.py tests/assistant_platform/test_agent_runtime.py
git commit -m "feat: add AgentRuntime tool loop for assistant chat"
```

---

### Task 6: Wire `generate_reply_text` to AgentRuntime

**Files:**
- Modify: `assistant_platform/conversation/orchestrator.py`
- Create: `tests/assistant_platform/test_orchestrator_agent.py`
- Update: `tests/assistant_platform/test_orchestrator.py`
- Update or skip: `tests/assistant_platform/test_orchestrator_pending.py`, `tests/assistant_platform/test_orchestrator_llm_fallback.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/assistant_platform/test_orchestrator_agent.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow

TEAM = "team-agent-orch"


def _cfg() -> AssistantConfig:
    return AssistantConfig(
        team_id=TEAM,
        assistant_id="xiaomai",
        memory_enabled=False,
        llm=AssistantLlmConfig(
            enabled=True, api_key="k", model="m", base_url="https://example.test/v1"
        ),
    )


def test_generate_reply_uses_agent_not_intent_matcher():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id="m1",
        assistant_id="xiaomai",
        team_id=TEAM,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        reply_endpoint={"member_id": "m1", "role": "member"},
        text_redacted="查下我的额度",
        occurred_at=datetime.now(timezone.utc),
    )
    incoming = IncomingEventRow(
        event_id=event.event_id,
        channel=event.channel,
        channel_message_id=event.channel_message_id,
        assistant_id=event.assistant_id,
        team_id=event.team_id,
        sender_channel_user_id=event.sender_channel_user_id,
        sender_display_name=event.sender_display_name,
        conversation_type=event.conversation_type,
        conversation_id=event.conversation_id,
        reply_endpoint_json=event.reply_endpoint,
        text_redacted=event.text_redacted,
        occurred_at=event.occurred_at,
    )
    db.add(incoming)
    db.flush()
    session_row, _msg = attach_user_message(db, event, incoming_event_id=incoming.id)
    db.commit()

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.return_value = {
        "content": "Agent 已处理",
        "tool_calls": [],
        "raw_assistant_message": {"role": "assistant", "content": "Agent 已处理"},
    }

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[],
        ):
            reply = generate_reply_text(
                db,
                config=_cfg(),
                incoming=incoming,
                text="查下我的额度",
                session_row=session_row,
            )
    assert reply == "Agent 已处理"
    assert fake_llm.complete_with_tools.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_orchestrator_agent.py::test_generate_reply_uses_agent_not_intent_matcher -v`  
Expected: FAIL (still old path)

- [ ] **Step 3: Rewrite `generate_reply_text`**

Replace the body so the main path is Agent-only. Sketch:

```python
def generate_reply_text(...):
    if incoming is None:
        return simple_reply(text)

    actor_member_id, role = _actor_from_incoming(incoming)
    subject_id = resolve_subject_id(
        member_id=actor_member_id,
        channel_user_id=incoming.sender_channel_user_id,
    )

    client = build_assistant_llm_client(config)
    if client is None:
        if session_row is not None:
            memory_reply = try_memory_reply(
                db_session,
                config=config,
                session_row=session_row,
                incoming=incoming,
                text=text,
                display_name=display_name,
            )
            if memory_reply:
                return memory_reply
        return "助手暂时不可用，请稍后再试。"

    capabilities = resolve_capabilities(
        db_session,
        team_id=incoming.team_id,
        role=role,
        member_id=actor_member_id,
    )
    llm_cfg = resolve_effective_llm(config)
    history: list[dict] = []
    if session_row is not None:
        history = load_session_history_messages(
            db_session,
            session_id=session_row.id,
            limit=llm_cfg.agent_history_max_messages,
        )
        # process_session_job already attached current user message — avoid duplicate
        if (
            history
            and history[-1].get("role") == "user"
            and history[-1].get("content") == text
        ):
            history = history[:-1]

    system = build_agent_system(
        prompt_studio_supplement=compose_system_supplement(
            db_session,
            session_row.prompt_release_id if session_row else None,
        ),
        capabilities=capabilities,
        subject_id=subject_id,
    )

    pulse = pulse_client or PulseCapabilityClient(
        base_url=config.pulse_base_url,
        internal_token=config.pulse_internal_token,
    )
    owns = pulse_client is None
    try:
        executor = CapabilityExecutor(
            session=db_session, config=config, pulse_client=pulse
        )
        runtime = AgentRuntime(
            llm=client,
            executor=executor,
            capabilities=list(capabilities),
            max_tool_rounds=llm_cfg.agent_max_tool_rounds,
            subject_id=subject_id,
        )
        return runtime.run(
            system=system,
            history=history,
            user_text=text,
            actor_member_id=actor_member_id,
            team_id=incoming.team_id,
            role=role,
        )
    except AgentUnavailable as exc:
        return str(exc) or "助手暂时不可用，请稍后再试。"
    finally:
        if owns:
            pulse.close()
```

Remove pending / `match_capability_intent` / `_try_llm_intent_reply` / `_try_llm_command_assist` from this function. Clean unused imports.

- [ ] **Step 4: Fix broken tests**

- `test_orchestrator.py`: LLM disabled → expect「助手暂时不可用」or update mocks; stop asserting `simple_reply`「额度」hints if path changed.
- `test_orchestrator_pending.py`: skip with reason `system pending removed; confirmation is model-side` or delete cases.
- `test_orchestrator_llm_fallback.py`: rewrite to agent or skip.

Run:

```bash
pytest tests/assistant_platform/test_orchestrator_agent.py tests/assistant_platform/test_orchestrator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/conversation/orchestrator.py tests/assistant_platform/test_orchestrator_agent.py tests/assistant_platform/test_orchestrator.py tests/assistant_platform/test_orchestrator_pending.py tests/assistant_platform/test_orchestrator_llm_fallback.py
git commit -m "feat: route DingTalk text replies through AgentRuntime"
```

---

### Task 7: Update default Prompt seed + bot-commands docs

**Files:**
- Modify: `assistant_platform/prompts/seed.py`
- Modify: `docs/bot-commands.md`
- Test: `tests/assistant_platform/test_prompt_seed_agent_wording.py`

- [ ] **Step 1: Write failing test**

```python
from assistant_platform.prompts.seed import _FRAGMENT_STUBS


def test_precepts_mention_tools_not_fixed_commands():
    precepts = next(f for f in _FRAGMENT_STUBS if f["key"] == "precepts.md")["content"]
    assert "工具" in precepts or "tool" in precepts.lower()
    assert "命令格式" not in precepts
```

- [ ] **Step 2: Run to see fail**

Run: `pytest tests/assistant_platform/test_prompt_seed_agent_wording.py -v`

- [ ] **Step 3: Update seed + docs**

Replace precepts stub with:

```text
戒律：
1. 不泄露密钥与隐私。
2. 不确定时先澄清；能力名称必须来自已授权 tools 的 display_name / description，不得编造。
3. 需要执行时调用对应 tool；高风险操作先征得用户明确同意再调用。
4. 用户问「能做什么/帮助」时，根据当前可用 tools 总结，不背固定口令表。
5. 尊重每个能力边界，不把 A 说成 B。
```

Update `docs/bot-commands.md` intro: natural language first; examples are suggestions; help is summarized from available tools.

- [ ] **Step 4: Run tests + commit**

```bash
pytest tests/assistant_platform/test_prompt_seed_agent_wording.py -v
git add assistant_platform/prompts/seed.py docs/bot-commands.md tests/assistant_platform/test_prompt_seed_agent_wording.py
git commit -m "docs: align prompt seed and bot commands with agent tools"
```

Ops note: production Prompt Studio release is DB data — publish a new release in UI after deploy.

---

### Task 8: Regression suite + deprecate old modules

**Files:**
- Touch as needed: `tests/test_command_cutover.py`, help/intent tests
- Add isolation test to `test_orchestrator_agent.py`
- Deprecate module docstrings on `intents.py`, `llm_intent.py`, `pending.py`

- [ ] **Step 1: Run related suite**

```bash
pytest tests/assistant_platform/ tests/test_command_cutover.py tests/test_capability_usage_query.py tests/test_capability_usage_self_read.py -q
```

Fix failures: prefer Agent mocks; keep Pulse handler tests intact.

- [ ] **Step 2: Add isolation regression**

Seed sessions for `u-a` and `u-b`; run agent for `u-a`; assert `complete_with_tools` message contents never include `u-b` secret text.

- [ ] **Step 3: Mark deprecated**

At top of `intents.py`, `llm_intent.py`, `pending.py`:

```python
"""DEPRECATED: DingTalk text path now uses AgentRuntime. Kept temporarily for rollback reference."""
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: harden agent path regressions and deprecate intent modules"
```

- [ ] **Step 5: Spec status**

When smoke-tested: set design spec status to `已批准并实施` (or `实施中` while rolling out).

---

## Spec coverage

| Spec requirement | Task |
|------------------|------|
| Full LLM tools, no pattern match | 6 |
| catalog → tools, exclude bot.help | 2 |
| max rounds configurable (default 20) | 1, 5 |
| Multi tool_calls per round | 5 (sequential invoke) |
| Open session history | 3, 6 |
| Prompt Studio inject | 5, 6 |
| Model-side confirmation (`confirmed=True`) | 5 |
| LLM down → no rule fallback | 6 |
| usage.query kept + prompt guidance | 5 policy |
| Per-user isolation + subject_id | 3, 6, 8 |
| Future memory hooks only | 3 (no memory.search) |
| Docs / seed precepts | 7 |

## Out of scope (do not implement in this plan)

- `memory.search` tool / per-user memory files
- True threaded parallel tool invoke
- Deleting `intents.py` / `llm_intent.py` / `pending.py` / `help.py`
- Changing DingTalk Channel local paths (启动 / 引导图 / CSV)
