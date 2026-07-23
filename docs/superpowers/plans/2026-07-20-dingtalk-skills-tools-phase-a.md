# DingTalk Skills + Tools — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SkillRegistry + 4 grouped skill docs, inject skill cards into Agent system prompt, and expose `load_skill_docs` as a local meta-tool — without changing Capability execution or skill↔tool system bindings.

**Architecture:** New `assistant_platform/skills/` package loads `catalog.yaml` and Markdown docs from disk. `SkillActorContext` derives audience (`member` / `manager` / `admin`) from role + authorized capability keys + optional pending-approval count. `build_agent_system` renders filtered skill cards instead of the flat capability catalog. `AgentRuntime` handles `load_skill_docs` locally (same pattern as memory tools). Orchestrator passes actor context; pending count defaults to `0` until Pulse wiring in Phase B.

**Tech Stack:** Python 3, PyYAML (stdlib-free: use minimal YAML loader or `yaml` if already in deps), pytest, existing AgentRuntime / agent_policy / orchestrator.

**Spec:** `docs/superpowers/specs/2026-07-20-dingtalk-skills-tools-design.md`

---

## File map

| Path | Responsibility |
|------|----------------|
| `assistant_platform/skills/__init__.py` | Package exports |
| `assistant_platform/skills/models.py` | `SkillCard`, `SkillActorContext`, doc section types |
| `assistant_platform/skills/registry.py` | Load catalog/docs; `list_cards`, `load_docs`; audience filter + truncate |
| `assistant_platform/skills/formatting.py` | Render skill cards block for system prompt |
| `assistant_platform/skills/agent_tools.py` | `load_skill_docs` OpenAI tool schema + local invoke helper |
| `assistant_platform/skills/catalog.yaml` | 4 skill cards: `cursor.self`, `key.loan`, `bot.guide`, `team.admin` |
| `assistant_platform/skills/docs/**` | Markdown docs (overview + tasks/* + admin where needed) |
| `assistant_platform/conversation/agent_policy.py` | Inject skill cards; reference `load_skill_docs` in rules |
| `assistant_platform/conversation/agent_tools.py` | Append skill meta-tool to tool list |
| `assistant_platform/conversation/agent_runtime.py` | Dispatch `load_skill_docs` locally |
| `assistant_platform/conversation/orchestrator.py` | Build `SkillActorContext`, pass to policy + runtime |
| `tests/assistant_platform/test_skill_registry.py` | Registry unit tests |
| `tests/assistant_platform/test_skill_agent_integration.py` | Policy + runtime integration tests |

**Parallelization lock-in:** Skill docs are read-only at runtime (file cache in registry). No DB tables in Phase A.

---

### Task 1: Skill models + catalog skeleton

**Files:**
- Create: `assistant_platform/skills/__init__.py`
- Create: `assistant_platform/skills/models.py`
- Create: `assistant_platform/skills/catalog.yaml`

- [ ] **Step 1: Write the failing test**

Create `tests/assistant_platform/test_skill_registry.py`:

```python
from assistant_platform.skills.models import SkillCard, SkillActorContext


def test_skill_actor_audiences_member_only():
    actor = SkillActorContext(
        member_id="m1",
        role="member",
        authorized_capability_keys=frozenset({"quota.self.read"}),
    )
    assert actor.audiences == frozenset({"member"})
    assert not actor.is_admin
    assert not actor.is_manager


def test_skill_actor_admin_and_manager():
    actor = SkillActorContext(
        member_id="m1",
        role="owner",
        authorized_capability_keys=frozenset(
            {"access.request.decide", "usage.aggregate"}
        ),
        pending_approval_count=2,
    )
    assert actor.is_admin
    assert actor.is_manager
    assert actor.audiences == frozenset({"member", "admin", "manager"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/assistant_platform/test_skill_registry.py::test_skill_actor_audiences_member_only -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`assistant_platform/skills/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


_ADMIN_MARKER_KEYS = frozenset(
    {
        "usage.aggregate",
        "report.publish",
        "submission.status.read",
        "members.manage",
        "alerts.run",
        "usage.export",
        "guide_image.update",
    }
)


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    name: str
    summary: str
    when_to_use: tuple[str, ...]
    audience: frozenset[str]
    aliases: tuple[str, ...] = ()
    privacy: str | None = None
    pending_hint: bool = False


@dataclass(frozen=True)
class SkillActorContext:
    member_id: str
    role: str | None
    authorized_capability_keys: frozenset[str]
    pending_approval_count: int = 0

    @property
    def is_admin(self) -> bool:
        if self.role in ("owner", "operator"):
            return True
        return bool(_ADMIN_MARKER_KEYS & self.authorized_capability_keys)

    @property
    def is_manager(self) -> bool:
        return "access.request.decide" in self.authorized_capability_keys

    @property
    def audiences(self) -> frozenset[str]:
        tags: set[str] = {"member"}
        if self.is_manager:
            tags.add("manager")
        if self.is_admin:
            tags.add("admin")
        return frozenset(tags)


@dataclass(frozen=True)
class SkillDocResult:
    skill_id: str
    markdown: str
    truncated: bool = False
```

`assistant_platform/skills/__init__.py`:

```python
from assistant_platform.skills.models import SkillActorContext, SkillCard, SkillDocResult
from assistant_platform.skills.registry import SkillRegistry

__all__ = ["SkillActorContext", "SkillCard", "SkillDocResult", "SkillRegistry"]
```

`assistant_platform/skills/catalog.yaml` (4 cards):

```yaml
skills:
  - skill_id: cursor.self
    name: 我的 Cursor
    summary: 查看本人 Cursor 用量、额度与提交记录；绑定或解绑 API Key。
    when_to_use:
      - 用户问「我的用量」「额度够不够」「有没有提交」
      - 用户要绑定/解绑 Cursor Key
      - 用户发送 crsr_ 开头的 Key（私聊）
    audience: [member]
    aliases: [cursor, 额度, 我的用量, 绑定]
    privacy: private

  - skill_id: key.loan
    name: 临时 Key 借用
    summary: 额度不足时私聊借用团队富余账号的临时 Cursor Key；可查看与归还。
    when_to_use:
      - 用户提到「借 key」「临时 key」「额度不够想继续写代码」
      - 用户问「我的借用」「归还 key」
    audience: [member]
    aliases: [借key, 借 Key, 归还 Key]
    privacy: private

  - skill_id: bot.guide
    name: 小脉能做什么
    summary: 查看当前可用的技能目录与用法入口。
    when_to_use:
      - 用户问「你能做什么」「有什么功能」「怎么用」
    audience: [member]
    aliases: [帮助, help, 功能列表]

  - skill_id: team.admin
    name: 团队运营管理
    summary: 月报、聚合、提交进度、成员、告警、导出、引导图等管理操作。
    when_to_use:
      - 管理员问团队状态、报告、聚合、导出、告警
    audience: [admin]
    aliases: [管理, 运营, 报告, 聚合]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/assistant_platform/test_skill_registry.py::test_skill_actor_audiences_member_only tests/assistant_platform/test_skill_registry.py::test_skill_actor_admin_and_manager -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/skills/ tests/assistant_platform/test_skill_registry.py
git commit -m "feat: add skill models and catalog skeleton for Phase A"
```

---

### Task 2: SkillRegistry — load cards, filter audience, load docs

**Files:**
- Create: `assistant_platform/skills/registry.py`
- Create: `assistant_platform/skills/docs/cursor.self/overview.md`
- Create: `assistant_platform/skills/docs/cursor.self/tasks/quota.md`
- Create: `assistant_platform/skills/docs/key.loan/overview.md`
- Create: `assistant_platform/skills/docs/key.loan/tasks/borrow.md`
- Create: `assistant_platform/skills/docs/key.loan/admin.md`
- Create: `assistant_platform/skills/docs/bot.guide/overview.md`
- Create: `assistant_platform/skills/docs/team.admin/overview.md`
- Modify: `tests/assistant_platform/test_skill_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/assistant_platform/test_skill_registry.py`:

```python
from pathlib import Path

from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry


def test_list_cards_filters_admin_skill():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    admin = SkillActorContext(
        "m1",
        "owner",
        frozenset({"usage.aggregate", "quota.self.read"}),
    )
    member_ids = {c.skill_id for c in reg.list_cards(member)}
    admin_ids = {c.skill_id for c in reg.list_cards(admin)}
    assert "team.admin" not in member_ids
    assert "team.admin" in admin_ids
    assert "cursor.self" in member_ids


def test_onboarding_pending_hint_only_when_pending():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    manager = SkillActorContext(
        "m1",
        "member",
        frozenset({"access.request.decide"}),
        pending_approval_count=0,
    )
    cards = {c.skill_id: c for c in reg.list_cards(manager)}
    assert "cursor.onboarding" not in cards  # not in Phase A catalog yet — skip if absent

    # After Task 3 adds cursor.onboarding to catalog, replace with:
    # assert "待审批" not in cards["cursor.onboarding"].summary
    # manager_pending = SkillActorContext(..., pending_approval_count=2)
    # hinted = reg.list_cards(manager_pending)
    # assert "待审批" in next(c for c in hinted if c.skill_id == "cursor.onboarding").summary


def test_load_docs_rejects_invisible_skill():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    try:
        reg.load_docs("team.admin", section="overview", actor=member)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "不可见" in str(exc) or "not visible" in str(exc).lower()


def test_load_docs_admin_section_hidden_from_member():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    admin = SkillActorContext("m1", "owner", frozenset({"usage.aggregate"}))
    member = SkillActorContext("m1", "member", frozenset({"key.loan.request"}))
    admin_doc = reg.load_docs("key.loan", section="all", actor=admin, token_budget=4000)
    member_doc = reg.load_docs("key.loan", section="all", actor=member, token_budget=4000)
    assert "key_loan_list" in admin_doc.markdown or "借用列表" in admin_doc.markdown
    assert "key_loan_list" not in member_doc.markdown
```

Minimal doc files (example `key.loan/admin.md`):

```markdown
---
audience: [admin]
---
## 借用列表与撤销

管理员可调用 tool `key_loan_list` 查看借用列表，`key_loan_revoke` 撤销借用。
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/assistant_platform/test_skill_registry.py -v`  
Expected: FAIL — `SkillRegistry` missing / docs not found

- [ ] **Step 3: Implement SkillRegistry**

`assistant_platform/skills/registry.py` core API:

```python
class SkillRegistry:
    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root or Path(__file__).resolve().parent
        self._cards = self._load_catalog(self._root / "catalog.yaml")

    def list_cards(self, actor: SkillActorContext) -> list[SkillCard]:
        visible: list[SkillCard] = []
        for card in self._cards.values():
            if not card.audience & actor.audiences:
                continue
            visible.append(self._apply_pending_hint(card, actor))
        return sorted(visible, key=lambda c: c.skill_id)

    def load_docs(
        self,
        skill_id: str,
        *,
        section: str = "overview,steps,examples",
        actor: SkillActorContext,
        token_budget: int = 4000,
    ) -> SkillDocResult:
        card = self._cards.get(skill_id)
        if card is None or not (card.audience & actor.audiences):
            raise ValueError(f"skill 对当前用户不可见: {skill_id}")
        paths = self._resolve_doc_paths(skill_id, section)
        parts: list[str] = []
        for path in paths:
            meta, body = self._parse_frontmatter(path)
            doc_audience = frozenset(meta.get("audience") or ["member"])
            if not (doc_audience & actor.audiences):
                continue
            parts.append(body.strip())
        markdown = "\n\n".join(p for p in parts if p)
        truncated = False
        if estimate_tokens(markdown) > token_budget:
            markdown = self._truncate_markdown(markdown, token_budget)
            truncated = True
            markdown += "\n\n<!-- truncated -->"
        return SkillDocResult(skill_id=skill_id, markdown=markdown, truncated=truncated)
```

Implementation notes:
- Reuse `estimate_tokens` from `assistant_platform.memory.archive_indexer`.
- `_resolve_doc_paths`: `overview` → `overview.md`; `steps` → all `tasks/*.md` sorted; `admin` → `admin.md`; `manager` → `manager.md`; `all` → union.
- `_parse_frontmatter`: split on leading `---` lines; YAML subset via manual key parsing or `import yaml` if project already depends on PyYAML — check `pyproject.toml` / `requirements.txt` first.
- `_apply_pending_hint`: if `card.pending_hint` and `actor.is_manager` and `actor.pending_approval_count > 0`, append `（当前有 N 条待审批申请）` to summary.

- [ ] **Step 4: Run tests**

Run: `pytest tests/assistant_platform/test_skill_registry.py -v`  
Expected: PASS (except onboarding hint test if card not yet added — OK until Task 3)

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/skills/registry.py assistant_platform/skills/docs/ tests/assistant_platform/test_skill_registry.py
git commit -m "feat: add SkillRegistry with audience filtering and doc loading"
```

---

### Task 3: Skill card formatting + agent_policy injection

**Files:**
- Create: `assistant_platform/skills/formatting.py`
- Modify: `assistant_platform/conversation/agent_policy.py`
- Modify: `tests/assistant_platform/test_agent_policy.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/assistant_platform/test_agent_policy.py`:

```python
from pathlib import Path

from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry


def test_policy_injects_skill_cards_not_flat_capability_catalog():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    actor = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    cards = reg.list_cards(actor)
    system = build_agent_system(
        prompt_studio_supplement="",
        capabilities=[_cap("quota.self.read")],
        subject_id="u1",
        conversation_type="private",
        skill_cards=cards,
        skills_enabled=True,
    )
    assert "## 可用技能" in system
    assert "cursor.self" in system
    assert "load_skill_docs" in system
    assert "## 当前可用能力（display_name）" not in system
    assert "quota.self.read" not in system or "tool=quota_self_read" not in system
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/assistant_platform/test_agent_policy.py::test_policy_injects_skill_cards_not_flat_capability_catalog -v`  
Expected: FAIL — unknown kwargs `skill_cards` / `skills_enabled`

- [ ] **Step 3: Implement formatting + policy changes**

`assistant_platform/skills/formatting.py`:

```python
def format_skill_cards_block(cards: list[SkillCard]) -> str:
    if not cards:
        return "## 可用技能\n\n（当前无可见技能卡片）"
    lines = ["## 可用技能", ""]
    for card in cards:
        lines.append(f"### {card.name} (`{card.skill_id}`)")
        lines.append(card.summary)
        if card.when_to_use:
            lines.append("when_to_use:")
            for item in card.when_to_use:
                lines.append(f"- {item}")
        if card.privacy == "private":
            lines.append("- 隐私：Key 相关操作建议私聊")
        lines.append("")
    return "\n".join(lines).strip()
```

Update `build_agent_system` signature:

```python
def build_agent_system(
    *,
    prompt_studio_supplement: str,
    capabilities: Iterable[ResolvedCapability],
    subject_id: str,
    conversation_type: str = "private",
    recall_bundle: RecallBundle | None = None,
    memory_tools_enabled: bool = False,
    skill_cards: list[SkillCard] | None = None,
    skills_enabled: bool = False,
) -> str:
```

Policy rule changes:
- Replace rule 2 with skill-first guidance + `load_skill_docs`.
- Replace rule 4 (usage_query priority) — remove from policy; move to `cursor.self` doc prose.
- When `skills_enabled` and `skill_cards` provided, append `format_skill_cards_block(skill_cards)` instead of the `## 当前可用能力` loop.
- Keep capability-derived rules that are still global (web search, manual submit, tip create).

- [ ] **Step 4: Run tests**

Run: `pytest tests/assistant_platform/test_agent_policy.py -v`  
Expected: PASS (update existing tests if they assert on old capability list text)

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/skills/formatting.py assistant_platform/conversation/agent_policy.py tests/assistant_platform/test_agent_policy.py
git commit -m "feat: inject skill cards into agent system prompt"
```

---

### Task 4: `load_skill_docs` meta-tool + AgentRuntime dispatch

**Files:**
- Create: `assistant_platform/skills/agent_tools.py`
- Modify: `assistant_platform/conversation/agent_tools.py`
- Modify: `assistant_platform/conversation/agent_runtime.py`
- Create: `tests/assistant_platform/test_skill_agent_integration.py`

- [ ] **Step 1: Write failing integration test**

`tests/assistant_platform/test_skill_agent_integration.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_runtime import AgentRuntime
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry
from tests.assistant_platform.test_agent_runtime import FakeLlm, _cap


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
                            {"skill_id": "cursor.self", "section": "overview"}
                        ),
                    }
                ],
                "raw_assistant_message": {},
            },
            {
                "content": "好的，我来查额度",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "好的，我来查额度"},
            },
        ]
    )
    executor = MagicMock()
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
    executor.invoke.assert_not_called()
    tool_msgs = [
        m for m in llm.calls[0]["messages"] if m.get("role") == "tool"
    ]
    assert not tool_msgs  # tool result appended internally; check second llm call messages
    second_msgs = llm.calls[1]["messages"]
    tool_payload = next(m for m in second_msgs if m.get("role") == "tool")
    data = json.loads(tool_payload["content"])
    assert data["ok"] is True
    assert "cursor.self" in data["skill_id"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/assistant_platform/test_skill_agent_integration.py -v`  
Expected: FAIL — `skill_registry` kwarg missing

- [ ] **Step 3: Implement meta-tool**

`assistant_platform/skills/agent_tools.py`:

```python
LOAD_SKILL_DOCS_TOOL_NAME = "load_skill_docs"

def load_skill_docs_tool_definition() -> dict:
    return {
        "type": "function",
        "function": {
            "name": LOAD_SKILL_DOCS_TOOL_NAME,
            "description": (
                "按需加载某项技能的 Markdown 说明书。"
                "先读技能卡片 when_to_use，不确定细节时再调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                    "section": {
                        "type": "string",
                        "description": "overview|steps|examples|admin|manager|all",
                    },
                },
                "required": ["skill_id"],
                "additionalProperties": False,
            },
        },
    }

def is_local_skill_tool(tool_name: str) -> bool:
    return tool_name == LOAD_SKILL_DOCS_TOOL_NAME

def invoke_load_skill_docs(registry, actor, arguments: str, *, token_budget: int) -> str:
    args = json.loads(arguments or "{}")
    if not isinstance(args, dict):
        args = {}
    skill_id = str(args.get("skill_id") or "").strip()
    section = str(args.get("section") or "overview,steps,examples").strip()
    try:
        result = registry.load_docs(
            skill_id,
            section=section,
            actor=actor,
            token_budget=token_budget,
        )
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(
        {
            "ok": True,
            "skill_id": result.skill_id,
            "truncated": result.truncated,
            "markdown": result.markdown,
        },
        ensure_ascii=False,
    )
```

Wire in `assistant_platform/conversation/agent_tools.py`:

```python
from assistant_platform.skills.agent_tools import load_skill_docs_tool_definition

def tools_from_capabilities(..., *, include_skill_tools: bool = False):
    ...
    if include_skill_tools:
        tools.append(load_skill_docs_tool_definition())
```

Wire in `AgentRuntime.__init__`:

```python
def __init__(..., skill_registry=None, skill_actor=None, skill_doc_token_budget: int = 4000):
    self._skill_registry = skill_registry
    self._skill_actor = skill_actor
    self._skill_doc_token_budget = skill_doc_token_budget
    self._tools = tools_from_capabilities(
        self._capabilities,
        include_memory_tools=memory_tools is not None,
        include_skill_tools=skill_registry is not None and skill_actor is not None,
    )
```

In tool dispatch loop, before memory tools:

```python
elif (
    is_local_skill_tool(name)
    and self._skill_registry is not None
    and self._skill_actor is not None
):
    payload = invoke_load_skill_docs(
        self._skill_registry,
        self._skill_actor,
        tc.get("arguments") or "{}",
        token_budget=self._skill_doc_token_budget,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/assistant_platform/test_skill_agent_integration.py tests/assistant_platform/test_agent_runtime.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/skills/agent_tools.py assistant_platform/conversation/agent_tools.py assistant_platform/conversation/agent_runtime.py tests/assistant_platform/test_skill_agent_integration.py
git commit -m "feat: add load_skill_docs local meta-tool to AgentRuntime"
```

---

### Task 5: Orchestrator wiring + feature flag

**Files:**
- Modify: `assistant_platform/conversation/orchestrator.py`
- Modify: `assistant_platform/config.py` (optional `skills_enabled: bool = True`)
- Modify: `tests/assistant_platform/test_orchestrator_agent.py` (if exists) or add smoke test

- [ ] **Step 1: Write failing smoke test**

```python
# tests/assistant_platform/test_orchestrator_skills.py
from unittest.mock import MagicMock, patch

from assistant_platform.conversation.orchestrator import generate_reply_text


def test_generate_reply_text_passes_skill_cards_when_enabled(db_session, ...):
    # Use existing orchestrator test fixtures; mock LLM to capture system prompt
    ...
    assert "## 可用技能" in captured_system
```

(Adapt to existing test DB fixtures in repo — copy pattern from `test_orchestrator_agent.py`.)

- [ ] **Step 2: Wire orchestrator**

In `generate_reply_text`, after resolving capabilities:

```python
from assistant_platform.skills.registry import SkillRegistry
from assistant_platform.skills.models import SkillActorContext

skill_registry = SkillRegistry()
skill_actor = SkillActorContext(
    member_id=actor_member_id,
    role=role,
    authorized_capability_keys=frozenset(c.key for c in capabilities),
    pending_approval_count=int((incoming.reply_endpoint_json or {}).get("pending_approval_count") or 0),
)
skill_cards = skill_registry.list_cards(skill_actor)
skills_enabled = getattr(config, "skills_enabled", True)  # or config.features.skills_enabled
```

Pass to `build_agent_system(..., skill_cards=skill_cards if skills_enabled else None, skills_enabled=skills_enabled)` and `AgentRuntime(..., skill_registry=skill_registry if skills_enabled else None, skill_actor=skill_actor if skills_enabled else None)`.

**Phase A pending count source:** read optional `pending_approval_count` from `reply_endpoint_json` (Channel mirror can populate in Phase B). Default `0`.

- [ ] **Step 3: Run targeted tests**

Run: `pytest tests/assistant_platform/test_orchestrator_agent.py tests/assistant_platform/test_skill_agent_integration.py tests/assistant_platform/test_agent_policy.py -v`  
Expected: PASS

- [ ] **Step 4: Full assistant_platform suite spot check**

Run: `pytest tests/assistant_platform/ -q --tb=no`  
Expected: no new failures

- [ ] **Step 5: Commit**

```bash
git add assistant_platform/conversation/orchestrator.py assistant_platform/config.py tests/assistant_platform/test_orchestrator_skills.py
git commit -m "feat: wire SkillRegistry into orchestrator and AgentRuntime"
```

---

### Task 6: Phase A doc content (4 skills)

**Files:**
- Expand: `assistant_platform/skills/docs/cursor.self/**`
- Expand: `assistant_platform/skills/docs/key.loan/**`
- Expand: `assistant_platform/skills/docs/bot.guide/overview.md`
- Expand: `assistant_platform/skills/docs/team.admin/**`

- [ ] **Step 1: Author docs from `help.py` / `docs/bot-commands.md`**

Minimum content per skill:
- `cursor.self`: tasks for 我的 / 我的用量 / 额度 / 绑 Key / 解绑 — prose mentions `submission_self_read`, `usage_self_read`, `quota_self_read`, `cursor_key_bind`, `cursor_key_unbind`.
- `key.loan`: borrow / return / my loan + admin list/revoke.
- `bot.guide`: index of other skill_ids + remind to call `load_skill_docs`.
- `team.admin`: skeleton tasks for 状态/聚合/报告/成员/告警/导出/引导图.

- [ ] **Step 2: Manual sanity via unit test**

Add test that loads each Phase A skill doc for admin/member without empty markdown:

```python
def test_phase_a_docs_non_empty():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    for skill_id, actor in [
        ("cursor.self", member_actor),
        ("key.loan", member_actor),
        ("bot.guide", member_actor),
        ("team.admin", admin_actor),
    ]:
        doc = reg.load_docs(skill_id, section="all", actor=actor)
        assert len(doc.markdown.strip()) > 50
```

- [ ] **Step 3: Commit**

```bash
git add assistant_platform/skills/docs/
git commit -m "docs: add Phase A skill markdown for cursor.self, key.loan, bot.guide, team.admin"
```

---

## Self-review (spec coverage)

| Spec requirement | Task |
|------------------|------|
| Skill cards in system prompt | Task 3 |
| `load_skill_docs` meta-tool | Task 4 |
| Audience filter (member/admin/manager) | Task 2 |
| Doc section audience filter | Task 2 |
| Doc truncation | Task 2 |
| No skill↔tool registry | All — prose only in docs |
| 4 Phase A skills | Task 1, 6 |
| Manager pending hint | Task 2 `_apply_pending_hint` + orchestrator count |
| Remove flat capability catalog from policy | Task 3 |

**Deferred to Phase B:** `cursor.onboarding`, remaining 4 member skills, `help.py` migration, Channel `pending_approval_count` population, full bot-commands diff.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-dingtalk-skills-tools-phase-a.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — implement tasks in this session with checkpoints

Which approach?
