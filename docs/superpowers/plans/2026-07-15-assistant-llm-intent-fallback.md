# Assistant LLM 意图兜底 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关键字意图未命中时，由 Assistant 侧 LLM 对**已授权能力**做分类与按需抽参，经 CapabilityExecutor 执行；低置信反问、敏感能力确认、LLM 关闭时与现网一致。

**Architecture:** 在 `generate_reply_text` 中保持「规则 →（可选）LLM → 记忆」顺序；新建 `assistant_platform/llm/` 客户端与 `conversation/llm_intent.py` 分类/抽参；会话 `session_state_json` 保存 pending 确认态；不 `import pulse.llm`。

**Tech Stack:** Python、Pydantic、`httpx`、pytest、现有 `resolve_capabilities` / `CapabilityExecutor`

**Spec:** `docs/superpowers/specs/2026-07-15-assistant-llm-intent-fallback-design.md`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `assistant_platform/config.py` | `AssistantLlmConfig` + env 加载 |
| `assistant_platform/llm/client.py` | OpenAI 兼容 `complete`（精简复制，不依赖 pulse） |
| `assistant_platform/llm/__init__.py` | `build_assistant_llm_client` |
| `assistant_platform/conversation/llm_intent.py` | 分类 / 抽参 / schema 校验 |
| `assistant_platform/conversation/pending.py` | 会话 pending 确认态读写与 TTL |
| `assistant_platform/conversation/models.py` | `ChatSessionRow.session_state_json` |
| `assistant_platform/conversation/orchestrator.py` | 编排挂点 |
| `tests/assistant_platform/test_llm_client.py` | 客户端构造 |
| `tests/assistant_platform/test_llm_intent.py` | 分类/抽参纯逻辑（mock LLM） |
| `tests/assistant_platform/test_orchestrator_llm_fallback.py` | 编排集成 |
| `.env.example` | `ASSISTANT_LLM_*` 文档 |

**前置（可单独 commit）：** 工作区未提交的「我借用的」规则修补（`commands.py` / `test_command_cutover.py`）建议在本计划前先合入，减少与 LLM fallback 混测。

---

### Task 1: Assistant LLM 配置与客户端

**Files:**
- Modify: `assistant_platform/config.py`
- Create: `assistant_platform/llm/__init__.py`
- Create: `assistant_platform/llm/client.py`
- Create: `tests/assistant_platform/test_llm_client.py`
- Modify: `.env.example`

- [ ] **Step 1: 写失败测试**

```python
# tests/assistant_platform/test_llm_client.py
from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.llm import build_assistant_llm_client


def test_llm_disabled_returns_none():
    cfg = AssistantConfig(team_id="t1", llm=AssistantLlmConfig(enabled=False))
    assert build_assistant_llm_client(cfg) is None


def test_llm_enabled_without_key_returns_none():
    cfg = AssistantConfig(
        team_id="t1",
        llm=AssistantLlmConfig(enabled=True, api_key="", model="gpt-4o-mini"),
    )
    assert build_assistant_llm_client(cfg) is None


def test_llm_enabled_with_key_returns_client():
    cfg = AssistantConfig(
        team_id="t1",
        llm=AssistantLlmConfig(
            enabled=True,
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
        ),
    )
    client = build_assistant_llm_client(cfg)
    assert client is not None
    assert client.model == "gpt-4o-mini"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/assistant_platform/test_llm_client.py -v
```

Expected: FAIL（模块/字段不存在）

- [ ] **Step 3: 实现配置与客户端**

`assistant_platform/config.py` 增加：

```python
class AssistantLlmConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    intent_min_confidence: float = 0.6
    timeout_seconds: float = 30.0


class AssistantConfig(BaseModel):
    ...
    llm: AssistantLlmConfig = Field(default_factory=AssistantLlmConfig)
```

`load_assistant_config()` 读取：

```python
llm=AssistantLlmConfig(
    enabled=os.environ.get("ASSISTANT_LLM_ENABLED", "false").lower()
    in ("1", "true", "yes", "on"),
    api_key=os.environ.get("ASSISTANT_LLM_API_KEY", "").strip(),
    base_url=os.environ.get("ASSISTANT_LLM_BASE_URL", "https://api.openai.com/v1").strip(),
    model=os.environ.get("ASSISTANT_LLM_MODEL", "").strip(),
    intent_min_confidence=float(
        os.environ.get("ASSISTANT_LLM_INTENT_MIN_CONFIDENCE", "0.6")
    ),
),
```

`assistant_platform/llm/client.py`（精简，仅 `complete`）：

```python
from __future__ import annotations

import httpx


class AssistantLlmClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, *, system: str, user: str, temperature: float = 0.1) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
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
        return data["choices"][0]["message"]["content"].strip()
```

`assistant_platform/llm/__init__.py`：

```python
from assistant_platform.config import AssistantConfig
from assistant_platform.llm.client import AssistantLlmClient


def build_assistant_llm_client(config: AssistantConfig) -> AssistantLlmClient | None:
    llm = config.llm
    if not llm.enabled or not llm.api_key or not llm.model:
        return None
    return AssistantLlmClient(
        api_key=llm.api_key,
        model=llm.model,
        base_url=llm.base_url,
        timeout_seconds=llm.timeout_seconds,
    )
```

`.env.example` 追加 `ASSISTANT_LLM_ENABLED=false` 等变量说明。

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/assistant_platform/test_llm_client.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add assistant_platform/config.py assistant_platform/llm/ tests/assistant_platform/test_llm_client.py .env.example
git commit -m "feat(assistant): add LLM config and OpenAI-compatible client"
```

---

### Task 2: 意图分类与抽参（纯逻辑 + mock LLM）

**Files:**
- Create: `assistant_platform/conversation/llm_intent.py`
- Create: `tests/assistant_platform/test_llm_intent.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/assistant_platform/test_llm_intent.py
from __future__ import annotations

import json
from unittest.mock import MagicMock

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.llm_intent import (
    IntentClassification,
    capability_needs_extraction,
    classify_intent,
    extract_arguments,
    normalize_classification,
)


def _caps():
    return [
        ResolvedCapability(
            key="key.loan.self.read",
            version="1",
            risk_level="read",
            display_name="查看借用",
            description="查看当前借入的 Key",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            confirmation_required=False,
        ),
        ResolvedCapability(
            key="cursor.key.bind",
            version="1",
            risk_level="sensitive",
            display_name="绑定 Key",
            description="绑定 Cursor API Key",
            input_schema={
                "type": "object",
                "required": ["api_key"],
                "properties": {
                    "api_key": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
            confirmation_required=True,
        ),
    ]


def test_capability_needs_extraction_when_required_fields_beyond_text():
    cap = _caps()[1]
    assert capability_needs_extraction(cap) is True
    assert capability_needs_extraction(_caps()[0]) is False


def test_normalize_rejects_unknown_key():
    allowed = {c.key: c for c in _caps()}
    raw = IntentClassification(
        decision="capability",
        capability_key="usage.export",
        confidence=0.9,
        clarify_question="",
        needs_args=False,
    )
    out = normalize_classification(raw, allowed=allowed, min_confidence=0.6)
    assert out.decision == "clarify"


def test_normalize_low_confidence_becomes_clarify():
    allowed = {c.key: c for c in _caps()}
    raw = IntentClassification(
        decision="capability",
        capability_key="key.loan.self.read",
        confidence=0.3,
        clarify_question="",
        needs_args=False,
    )
    out = normalize_classification(raw, allowed=allowed, min_confidence=0.6)
    assert out.decision == "clarify"


def test_classify_intent_parses_json_from_llm():
    client = MagicMock()
    client.complete.return_value = json.dumps(
        {
            "decision": "capability",
            "capability_key": "key.loan.self.read",
            "confidence": 0.92,
            "clarify_question": "",
            "needs_args": False,
        },
        ensure_ascii=False,
    )
    result = classify_intent(
        client,
        text="查看我借用的key",
        capabilities=_caps(),
        min_confidence=0.6,
    )
    assert result.decision == "capability"
    assert result.capability_key == "key.loan.self.read"


def test_extract_arguments_returns_dict():
    client = MagicMock()
    client.complete.return_value = json.dumps({"api_key": "sk-abc", "text": "绑定 sk-abc"})
    cap = _caps()[1]
    args = extract_arguments(client, text="绑定 sk-abc", capability=cap)
    assert args["api_key"] == "sk-abc"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/assistant_platform/test_llm_intent.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `llm_intent.py`**

核心类型与函数：

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from assistant_platform.capabilities.resolve import ResolvedCapability

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


class LlmCompleter(Protocol):
    def complete(self, *, system: str, user: str, temperature: float = 0.1) -> str: ...


@dataclass(frozen=True)
class IntentClassification:
    decision: str  # capability | chat | clarify
    capability_key: str | None
    confidence: float
    clarify_question: str
    needs_args: bool


def capability_needs_extraction(cap: ResolvedCapability) -> bool:
    schema = cap.input_schema or {}
    required = set(schema.get("required") or [])
    props = set((schema.get("properties") or {}).keys())
    extra_required = required - {"text"}
    extra_props = props - {"text"}
    return bool(extra_required or (extra_props and "text" not in props))


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty llm response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


def _catalog_lines(caps: list[ResolvedCapability]) -> str:
    lines = []
    for cap in caps:
        lines.append(
            f"- {cap.key} ({cap.risk_level}): {cap.display_name} — {cap.description}"
        )
    return "\n".join(lines)


def classify_intent(
    client: LlmCompleter,
    *,
    text: str,
    capabilities: list[ResolvedCapability],
    min_confidence: float,
    recent_turns: list[str] | None = None,
) -> IntentClassification:
    allowed = {c.key: c for c in capabilities}
    system = (
        "你是小脉的意图分类器。根据用户消息，从下列已授权能力中选择一项，"
        "或判定为闲聊(chat)，或在不确定时澄清(clarify)。"
        "只输出 JSON，不要其它文字。字段："
        "decision, capability_key, confidence, clarify_question, needs_args。"
        "capability_key 必须为下列 key 之一或 null。"
        "禁止编造未列出的能力。"
    )
    user_parts = [
        "已授权能力：",
        _catalog_lines(capabilities),
        "",
        f"用户消息：{text}",
    ]
    if recent_turns:
        user_parts.extend(["", "最近上下文：", *recent_turns[-2:]])
    raw = client.complete(system=system, user="\n".join(user_parts))
    data = _parse_json_object(raw)
    result = IntentClassification(
        decision=str(data.get("decision") or "chat"),
        capability_key=data.get("capability_key"),
        confidence=float(data.get("confidence") or 0.0),
        clarify_question=str(data.get("clarify_question") or ""),
        needs_args=bool(data.get("needs_args")),
    )
    return normalize_classification(result, allowed=allowed, min_confidence=min_confidence)


def normalize_classification(
    raw: IntentClassification,
    *,
    allowed: dict[str, ResolvedCapability],
    min_confidence: float,
) -> IntentClassification:
    if raw.decision == "capability":
        key = raw.capability_key
        if not key or key not in allowed:
            return IntentClassification(
                decision="clarify",
                capability_key=None,
                confidence=raw.confidence,
                clarify_question=raw.clarify_question or "我没太理解你的需求，能再说具体一点吗？",
                needs_args=False,
            )
        if raw.confidence < min_confidence:
            return IntentClassification(
                decision="clarify",
                capability_key=key,
                confidence=raw.confidence,
                clarify_question=raw.clarify_question or "你是想执行哪项操作？",
                needs_args=False,
            )
        cap = allowed[key]
        needs_args = capability_needs_extraction(cap)
        return IntentClassification(
            decision="capability",
            capability_key=key,
            confidence=raw.confidence,
            clarify_question="",
            needs_args=needs_args,
        )
    if raw.decision == "clarify":
        return IntentClassification(
            decision="clarify",
            capability_key=None,
            confidence=raw.confidence,
            clarify_question=raw.clarify_question or "能再说具体一点吗？",
            needs_args=False,
        )
    return IntentClassification(
        decision="chat",
        capability_key=None,
        confidence=raw.confidence,
        clarify_question="",
        needs_args=False,
    )


def extract_arguments(
    client: LlmCompleter,
    *,
    text: str,
    capability: ResolvedCapability,
) -> dict[str, Any]:
    schema = capability.input_schema or {}
    system = (
        "根据用户消息，为该能力提取 JSON 参数。"
        "只输出 JSON 对象，字段必须来自 schema，禁止多余字段。"
        f"schema={json.dumps(schema, ensure_ascii=False)}"
    )
    raw = client.complete(system=system, user=text)
    data = _parse_json_object(raw)
    if "text" not in data:
        data["text"] = text
    required = set(schema.get("required") or [])
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    allowed_props = set((schema.get("properties") or {}).keys())
    return {k: v for k, v in data.items() if k in allowed_props or k == "text"}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/assistant_platform/test_llm_intent.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add assistant_platform/conversation/llm_intent.py tests/assistant_platform/test_llm_intent.py
git commit -m "feat(assistant): add LLM intent classify and extract helpers"
```

---

### Task 3: 会话 pending 确认态

**Files:**
- Modify: `assistant_platform/conversation/models.py`
- Create: `assistant_platform/conversation/pending.py`
- Create: `tests/assistant_platform/test_pending_capability.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/assistant_platform/test_pending_capability.py
from datetime import datetime, timedelta, timezone

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.conversation.pending import (
    clear_pending_capability,
    get_pending_capability,
    set_pending_capability,
)


def _session() -> ChatSessionRow:
    return ChatSessionRow(
        assistant_id="xiaomai",
        team_id="t1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
    )


def test_set_and_get_pending():
    row = _session()
    set_pending_capability(
        row,
        capability_key="cursor.key.bind",
        arguments={"text": "绑定", "api_key": "sk-x"},
    )
    pending = get_pending_capability(row)
    assert pending is not None
    assert pending["capability_key"] == "cursor.key.bind"


def test_pending_expires_after_ttl():
    row = _session()
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    row.session_state_json = {
        "pending_capability": {
            "capability_key": "cursor.key.bind",
            "arguments": {"text": "x"},
            "created_at": past,
        }
    }
    assert get_pending_capability(row, ttl_seconds=300) is None
    assert row.session_state_json.get("pending_capability") is None


def test_clear_pending():
    row = _session()
    set_pending_capability(row, capability_key="k", arguments={"text": "x"})
    clear_pending_capability(row)
    assert get_pending_capability(row) is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/assistant_platform/test_pending_capability.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现**

`models.py` 为 `ChatSessionRow` 增加：

```python
session_state_json: Mapped[dict] = mapped_column(JSON, default=dict)
```

`pending.py`：

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from assistant_platform.conversation.models import ChatSessionRow

_PENDING_KEY = "pending_capability"
_DEFAULT_TTL_SECONDS = 300


def set_pending_capability(
    session_row: ChatSessionRow,
    *,
    capability_key: str,
    arguments: dict[str, Any],
) -> None:
    state = dict(session_row.session_state_json or {})
    state[_PENDING_KEY] = {
        "capability_key": capability_key,
        "arguments": arguments,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    session_row.session_state_json = state


def clear_pending_capability(session_row: ChatSessionRow) -> None:
    state = dict(session_row.session_state_json or {})
    state.pop(_PENDING_KEY, None)
    session_row.session_state_json = state


def get_pending_capability(
    session_row: ChatSessionRow,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any] | None:
    state = session_row.session_state_json or {}
    pending = state.get(_PENDING_KEY)
    if not pending:
        return None
    created_raw = pending.get("created_at")
    if created_raw:
        created = datetime.fromisoformat(created_raw)
        if datetime.now(timezone.utc) - created > timedelta(seconds=ttl_seconds):
            clear_pending_capability(session_row)
            return None
    return pending
```

SQLite 已有表由 `create_all` 补列：若本地 assistant.db 缺列，开发环境可删库重建或手写 `ALTER TABLE`（计划内注明在 RUNBOOK/README 一句即可，不强制迁移框架）。

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/assistant_platform/test_pending_capability.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add assistant_platform/conversation/models.py assistant_platform/conversation/pending.py tests/assistant_platform/test_pending_capability.py
git commit -m "feat(assistant): add session pending capability state"
```

---

### Task 4: Orchestrator 挂接 LLM fallback

**Files:**
- Modify: `assistant_platform/conversation/orchestrator.py`
- Create: `tests/assistant_platform/test_orchestrator_llm_fallback.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/assistant_platform/test_orchestrator_llm_fallback.py
from unittest.mock import MagicMock, patch

from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.llm_intent import IntentClassification
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.storage.models import IncomingEventRow


def _incoming():
    return IncomingEventRow(
        event_id="e1",
        channel="dingtalk",
        channel_message_id="m1",
        assistant_id="xiaomai",
        team_id="team-orchestrator",
        sender_channel_user_id="member-1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="member-1",
        reply_endpoint_json={"member_id": "member-1", "role": "ai_member"},
        text_redacted="查看我借用的key",
    )


def test_llm_disabled_falls_through_to_simple_reply(db_session):
    config = AssistantConfig(
        team_id="team-orchestrator",
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=False),
    )
    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="随便聊聊",
        session_row=None,
    )
    assert "额度" in reply


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
@patch("assistant_platform.conversation.orchestrator.resolve_capabilities")
def test_llm_classify_invokes_capability(
    mock_resolve,
    mock_build_client,
    mock_classify,
    db_session,
):
    from assistant_platform.capabilities.resolve import ResolvedCapability

    mock_build_client.return_value = MagicMock()
    mock_resolve.return_value = [
        ResolvedCapability(
            key="key.loan.self.read",
            version="1",
            risk_level="read",
            display_name="借用",
            description="",
            confirmation_required=False,
        )
    ]
    mock_classify.return_value = IntentClassification(
        decision="capability",
        capability_key="key.loan.self.read",
        confidence=0.9,
        clarify_question="",
        needs_args=False,
    )
    config = AssistantConfig(
        team_id="team-orchestrator",
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="📎 当前借用：无",
    )
    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="查看我借用的key",
        session_row=ChatSessionRow(
            assistant_id="xiaomai",
            team_id="team-orchestrator",
            channel="dingtalk",
            conversation_type="private",
            conversation_id="c1",
        ),
        pulse_client=pulse_client,
    )
    assert "借用" in reply
    pulse_client.invoke.assert_called_once()


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
@patch("assistant_platform.conversation.orchestrator.resolve_capabilities")
def test_clarify_does_not_call_memory(
    mock_resolve,
    mock_build_client,
    mock_classify,
    db_session,
):
    mock_build_client.return_value = MagicMock()
    mock_resolve.return_value = []
    mock_classify.return_value = IntentClassification(
        decision="clarify",
        capability_key=None,
        confidence=0.4,
        clarify_question="你是想查借入还是申请？",
        needs_args=False,
    )
    config = AssistantConfig(
        team_id="team-orchestrator",
        memory_enabled=True,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    with patch(
        "assistant_platform.conversation.orchestrator.try_memory_reply"
    ) as mock_memory:
        reply = generate_reply_text(
            db_session,
            config=config,
            incoming=_incoming(),
            text="key 的事",
            session_row=ChatSessionRow(
                assistant_id="xiaomai",
                team_id="team-orchestrator",
                channel="dingtalk",
                conversation_type="private",
                conversation_id="c1",
                user_id="u1",
            ),
        )
        mock_memory.assert_not_called()
    assert "借入" in reply or "申请" in reply
```

（`db_session` fixture：复用 `init_assistant_db("sqlite://", team_id="team-orchestrator")` 模式，与 `test_orchestrator.py` 一致。）

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/assistant_platform/test_orchestrator_llm_fallback.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 orchestrator 挂接**

在 `generate_reply_text` 中按顺序：

```python
from assistant_platform.llm import build_assistant_llm_client
from assistant_platform.capabilities.resolve import resolve_capabilities
from assistant_platform.conversation.llm_intent import (
    classify_intent,
    extract_arguments,
    capability_needs_extraction,
)
from assistant_platform.conversation.pending import (
    clear_pending_capability,
    get_pending_capability,
    set_pending_capability,
)

_CONFIRM_WORDS = frozenset({"确认", "确定", "是的", "好", "ok", "OK"})
_CANCEL_WORDS = frozenset({"取消", "不要", "算了", "否"})


def _handle_pending_confirmation(...)-> str | None:
    ...

def _try_llm_intent_reply(...)-> str | None:
    ...
```

逻辑要点：

1. **pending 优先**：`session_row` 有 pending 且 `text.strip()` ∈ 确认/取消 → 确认则 `confirmed=True` invoke 并 `clear_pending`；取消则 clear + 友好文案。
2. **规则 intent**（现有代码，不变）。
3. **LLM**：`client = build_assistant_llm_client(config)`；无 client → 跳过。
4. `resolve_capabilities(session, team_id, role, member_id=actor_member_id)` → 列表传入 `classify_intent`。
5. `decision=clarify` → 直接返回 `clarify_question`（**不** `try_memory_reply`）。
6. `decision=chat` → 落入后续记忆路径。
7. `decision=capability`：
   - 若 `needs_args` → `extract_arguments`；`ValueError` → 返回缺参提示。
   - 若 `confirmation_required` → `set_pending_capability` + 返回确认文案（含能力 display_name）。
   - 否则 `_try_capability_reply(..., confirmed=not confirmation_required)`。
8. LLM 异常 → `logger.warning` + 继续记忆路径。

确认文案示例：

```text
将执行：绑定 Cursor Key。
回复「确认」继续，「取消」中止。
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/assistant_platform/test_orchestrator_llm_fallback.py tests/assistant_platform/test_orchestrator.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add assistant_platform/conversation/orchestrator.py tests/assistant_platform/test_orchestrator_llm_fallback.py
git commit -m "feat(assistant): wire LLM intent fallback into orchestrator"
```

---

### Task 5: 回归、文档与 spec 状态

**Files:**
- Modify: `docs/superpowers/specs/2026-07-15-assistant-llm-intent-fallback-design.md`
- Modify: `README.md` 或 `docs/RUNBOOK.md`（`ASSISTANT_LLM_*` 开启说明，一段即可）

- [ ] **Step 1: 跑 Assistant 相关回归**

```bash
pytest tests/assistant_platform/ -v --import-mode=importlib
pytest tests/test_command_cutover.py -v --import-mode=importlib
```

Expected: PASS

- [ ] **Step 2: 更新 spec 状态为「已批准」；勾选验收清单中已实现项**

- [ ] **Step 3: Commit（仅当用户要求时）**

```bash
git add docs/
git commit -m "docs: mark assistant LLM intent fallback spec approved"
```

---

## Spec 覆盖自检

| Spec 要求 | Task |
|-----------|------|
| 关键字优先 | Task 4（规则分支在前） |
| miss 后 LLM 分类 | Task 2 + 4 |
| 需参再抽参 | Task 2 + 4 |
| 低置信 clarify | Task 2 `normalize` + Task 4 |
| sensitive 确认态 | Task 3 + 4 + Executor `confirmation_required` |
| 闲聊才进记忆 | Task 4 clarify 跳过 memory |
| Assistant LLM 配置独立 | Task 1 |
| 不 import pulse.llm | Task 1 |
| LLM 关断行为不变 | Task 4 测试 |
| 审计日志 | Task 4 内 `logger.info` 记录 decision/key/confidence（无密钥） |

## 执行说明

- 默认 `ASSISTANT_LLM_ENABLED=false`；本地验证需配置 `ASSISTANT_LLM_API_KEY` / `MODEL` 并重启 assistant 进程。
- 提交步骤遵循用户规则：**仅在被明确要求时 commit**。
- 建议顺序：Task 1 → 2 → 3 → 4 → 5；Task 2 可与 Task 3 并行，Task 4 依赖前三项。
