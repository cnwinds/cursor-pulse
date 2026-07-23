# Admin Skills/Tools/Prompt 只读对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 助手中心对齐 Skills/Tools：Skill 与 Prompt 以仓库文件为真源并只读展示；Capability 授权保留可写；Prompt 运行时脱离 DB release。

**Architecture:** Prompt 片段落到 `assistant_platform/prompts/docs/`，`compose_system_supplement` 改读盘；Assistant 新增 Skills/Prompts 只读 API，Pulse 代理到 `/api/v2/assistant/*`；web-admin 新增技能一览、Prompt 一览，能力中心改名为工具授权并去掉 Prompt 写 UI；旧 Prompt 写接口返回 410。

**Tech Stack:** Python/FastAPI、SQLAlchemy（仅 assignments）、Vue 3 + Element Plus、pytest、PyYAML

## Global Constraints

- Skill / Prompt **内容不进运营 DB**；文件为唯一真源
- Skill 可见叙事与 Tool 执行权 **严格分离**
- 后台对 Skill/Prompt **只读**；仅 Capability **assignments** 可写
- 本期 **不删** `ap_prompt_*` 表，但停止写入；写 API → 410
- 文案：改技能/Prompt → 仓库发版；改执行权 → 工具授权页

## File map

| 文件 | 职责 |
|------|------|
| `assistant_platform/prompts/docs/heart.md` | 人设片段 |
| `assistant_platform/prompts/docs/precepts.md` | 语气片段 |
| `assistant_platform/prompts/manifest.yaml` | 片段顺序 |
| `assistant_platform/prompts/loader.py` | 读盘 + compose |
| `assistant_platform/prompts/compose.py` | 薄封装，委托 loader（兼容旧 import） |
| `assistant_platform/prompts/fragments.py` | 保留 legacy 检测 helper；`CANONICAL_FRAGMENTS` 改为从文件加载或删除 |
| `assistant_platform/api/skills_admin.py` | Skills 只读 API |
| `assistant_platform/api/prompts.py` | 改只读 + 写接口 410 |
| `pulse/web/assistant_skills_api.py` | Pulse 代理 Skills |
| `pulse/web/assistant_prompts_api.py` | 代理只读；写路由 410 |
| `pulse/web/permissions.py` | 增加 `assistant:skills:read`；废弃 prompts write 映射 |
| `web-admin/src/views/SkillsView.vue` | 技能一览 |
| `web-admin/src/views/PromptsView.vue` | Prompt 一览（替换 Studio 编辑） |
| `web-admin/src/views/CapabilitiesView.vue` | 文案改为工具授权 |
| `web-admin/src/router/index.ts` / `MainLayout.vue` | 路由与菜单 |

---

### Task 1: Prompt 文件真源 + loader

**Files:**
- Create: `assistant_platform/prompts/docs/heart.md`
- Create: `assistant_platform/prompts/docs/precepts.md`
- Create: `assistant_platform/prompts/manifest.yaml`
- Create: `assistant_platform/prompts/loader.py`
- Modify: `assistant_platform/prompts/compose.py`
- Modify: `assistant_platform/prompts/fragments.py`
- Test: `tests/assistant_platform/test_prompt_compose.py`

**Interfaces:**
- Produces: `load_prompt_fragments_from_files() -> dict[str, str]`
- Produces: `compose_system_supplement_from_files() -> str`
- Produces: `compose_system_supplement(db_session=None, release_id=None) -> str`（忽略 DB，读文件；保留签名以减少调用方改动）

- [ ] **Step 1: Write failing tests for file-based compose**

Replace `tests/assistant_platform/test_prompt_compose.py` with:

```python
from __future__ import annotations

from assistant_platform.prompts.compose import compose_system_supplement, load_prompt_fragments
from assistant_platform.prompts.loader import load_prompt_fragments_from_files


def test_load_prompt_fragments_from_files_has_heart_and_precepts():
    fragments = load_prompt_fragments_from_files()
    assert "heart.md" in fragments
    assert "precepts.md" in fragments
    assert "小脉" in fragments["heart.md"]
    assert "人设与表达" in fragments["precepts.md"]


def test_compose_system_supplement_reads_files_without_db():
    supplement = compose_system_supplement(None, None)
    assert "人设与语气补充" in supplement
    assert "precepts.md" in supplement
    assert "小脉" in supplement


def test_load_prompt_fragments_ignores_release_id():
    # Backward-compatible signature; must not require DB
    fragments = load_prompt_fragments(None, "any-id")
    assert "heart.md" in fragments
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests/assistant_platform/test_prompt_compose.py -v`  
Expected: FAIL（`loader` 不存在或 compose 仍依赖 DB）

- [ ] **Step 3: Create prompt docs + manifest**

`assistant_platform/prompts/manifest.yaml`:

```yaml
fragments:
  - key: heart.md
    path: docs/heart.md
    description: 人设与基本语气
  - key: precepts.md
    path: docs/precepts.md
    description: 表达风格补充
```

`docs/heart.md` / `docs/precepts.md`：内容从现有 `CANONICAL_FRAGMENTS` 原样拷贝（去掉 Python 字符串拼接，保留中文正文）。

- [ ] **Step 4: Implement loader.py**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_PROMPTS_ROOT = Path(__file__).resolve().parent

PERSONA_SUPPLEMENT_HEADER = (
    "## Prompt Studio（人设与语气补充）\n"
    "以下内容仅调整人设与表达风格；"
    "工具调用、权限、交互节奏与业务流程以系统前文规则为准。"
)


def _manifest_path() -> Path:
    return _PROMPTS_ROOT / "manifest.yaml"


def load_manifest() -> list[dict[str, Any]]:
    raw = yaml.safe_load(_manifest_path().read_text(encoding="utf-8")) or {}
    return list(raw.get("fragments") or [])


def load_prompt_fragments_from_files(*, root: Path | None = None) -> dict[str, str]:
    base = root or _PROMPTS_ROOT
    items = load_manifest() if root is None else (
        yaml.safe_load((base / "manifest.yaml").read_text(encoding="utf-8")) or {}
    ).get("fragments") or []
    out: dict[str, str] = {}
    for item in items:
        key = str(item["key"]).strip()
        rel = str(item["path"]).strip()
        path = base / rel
        if not path.is_file():
            raise FileNotFoundError(f"prompt fragment missing: {path}")
        out[key] = path.read_text(encoding="utf-8").strip()
    return out


def compose_system_supplement_from_files(*, root: Path | None = None) -> str:
    fragments = load_prompt_fragments_from_files(root=root)
    parts: list[str] = []
    for key in ("heart.md", "precepts.md"):
        content = fragments.get(key, "").strip()
        if content:
            parts.append(f"## {key}\n{content}")
    if not parts:
        return ""
    return PERSONA_SUPPLEMENT_HEADER + "\n\n" + "\n\n".join(parts)
```

- [ ] **Step 5: Update compose.py to delegate to files**

```python
from __future__ import annotations

from typing import Any

from assistant_platform.prompts.loader import (
    PERSONA_SUPPLEMENT_HEADER,
    compose_system_supplement_from_files,
    load_prompt_fragments_from_files,
)

# re-export for tests/imports
__all__ = [
    "PERSONA_SUPPLEMENT_HEADER",
    "compose_system_supplement",
    "load_prompt_fragments",
]


def load_prompt_fragments(db_session: Any = None, release_id: str | None = None) -> dict[str, str]:
    return load_prompt_fragments_from_files()


def compose_system_supplement(db_session: Any = None, release_id: str | None = None) -> str:
    return compose_system_supplement_from_files()
```

- [ ] **Step 6: Update fragments.py**

Keep `LEGACY_*` helpers and `is_*_content`. Change `CANONICAL_FRAGMENTS` to:

```python
def canonical_fragments() -> list[dict[str, str]]:
    from assistant_platform.prompts.loader import load_prompt_fragments_from_files
    return [{"key": k, "content": v} for k, v in load_prompt_fragments_from_files().items()]

CANONICAL_FRAGMENTS = None  # deprecated; use canonical_fragments()
```

Update `seed.py` and any `for stub in CANONICAL_FRAGMENTS` to `canonical_fragments()` **only if still needed for tests**; prefer making seed a no-op in Task 2.

- [ ] **Step 7: Run tests**

Run: `.venv\Scripts\pytest.exe tests/assistant_platform/test_prompt_compose.py -v`  
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add assistant_platform/prompts/docs assistant_platform/prompts/manifest.yaml assistant_platform/prompts/loader.py assistant_platform/prompts/compose.py assistant_platform/prompts/fragments.py tests/assistant_platform/test_prompt_compose.py
git commit -m "feat: load persona prompts from files instead of DB release"
```

---

### Task 2: 运行时停用 DB prompt pin；seed 停写

**Files:**
- Modify: `assistant_platform/conversation/orchestrator.py`（compose 调用可简化）
- Modify: `assistant_platform/conversation/session_store.py`
- Modify: `assistant_platform/prompts/seed.py`
- Modify: `tests/assistant_platform/test_prompt_releases.py`
- Modify: `tests/assistant_platform/test_canary_deploy.py`（标记 skip 或改断言）

**Interfaces:**
- Consumes: `compose_system_supplement()` 文件版
- Produces: 新会话 `prompt_release_id=None`；`seed_default_prompt_release` 不再创建行（或返回 None）

- [ ] **Step 1: Write/adjust failing expectations**

In `test_prompt_releases.py`，将 `test_new_session_pins_production_prompt_release_id` 改为：

```python
def test_new_session_does_not_require_prompt_release():
    # ... create session via session_store helper used in existing test ...
    assert session_row.prompt_release_id is None
```

Skip or delete canary-specific tests that assert DB release routing for persona（`test_canary_deploy.py` 相关断言改为 `@pytest.mark.skip(reason="prompt release pipeline retired")`）。

- [ ] **Step 2: Run to see failures**

Run: `.venv\Scripts\pytest.exe tests/assistant_platform/test_prompt_releases.py tests/assistant_platform/test_canary_deploy.py -v`

- [ ] **Step 3: session_store 停止 pin release**

在创建会话处：

```python
# was: pinned_release = resolve_prompt_release_for_new_session(...)
prompt_release_id=None
```

删除对 `resolve_prompt_release_for_new_session` 的调用（可保留函数但标注 deprecated）。

- [ ] **Step 4: seed 停写**

`seed_default_prompt_release`：若已有 production 则原样返回；若无则 **不创建**，返回 `None`。  
`upgrade_production_to_*`：改为 no-op 返回 `get_production_release(session)`。  
启动路径调用 seed 处保持不炸。

可选：在 assistant 启动时若 DB production 内容与文件 hash 不一致，`logger.warning("ap_prompt production differs from files; files are source of truth")`。

- [ ] **Step 5: orchestrator**

```python
prompt_studio_supplement=compose_system_supplement(),
```

- [ ] **Step 6: Run tests**

Run: `.venv\Scripts\pytest.exe tests/assistant_platform/test_prompt_compose.py tests/assistant_platform/test_prompt_releases.py tests/assistant_platform/test_canary_deploy.py -v`  
Expected: PASS（或 skip 明确）

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor: stop pinning DB prompt releases for new sessions"
```

---

### Task 3: SkillRegistry 管理端枚举 + Skills 只读 API

**Files:**
- Modify: `assistant_platform/skills/registry.py`
- Create: `assistant_platform/api/skills_admin.py`
- Modify: `assistant_platform/api/app.py`（register routes）
- Test: `tests/assistant_platform/test_skills_admin_api.py`

**Interfaces:**
- Produces: `SkillRegistry.list_all_cards() -> list[SkillCard]`
- Produces: `SkillRegistry.list_doc_files(skill_id) -> list[dict]` with `section`, `rel_path`, `exists`
- Produces: `SkillRegistry.read_doc_file(skill_id, rel_path) -> str`
- API: `GET /api/assistant/v1/skills`, `GET /api/assistant/v1/skills/{skill_id}`, `GET /api/assistant/v1/skills/help-topics`

- [ ] **Step 1: Failing unit/API tests**

```python
def test_list_all_cards_includes_admin_skills():
    from assistant_platform.skills.registry import SkillRegistry
    cards = SkillRegistry().list_all_cards()
    ids = {c.skill_id for c in cards}
    assert "cursor.self" in ids
    assert "team.admin" in ids


def test_skills_list_endpoint(client):  # use existing assistant TestClient fixture if any
    r = client.get("/api/assistant/v1/skills")
    assert r.status_code == 200
    body = r.json()
    assert "skills" in body
    assert any(s["skill_id"] == "bot.guide" for s in body["skills"])
```

若无 TestClient fixture，用 `SkillRegistry` 单测 + 轻量 FastAPI 路由单测（参考 `tests/assistant_platform` 现有 API 测法）。

- [ ] **Step 2: Implement registry admin helpers**

```python
def list_all_cards(self) -> list[SkillCard]:
    return sorted(self._cards.values(), key=lambda c: c.skill_id)

def list_doc_files(self, skill_id: str) -> list[dict[str, object]]:
    root = self._docs_root / skill_id
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(self._docs_root).as_posix()
        rows.append({
            "section": path.stem,
            "rel_path": f"assistant_platform/skills/docs/{rel}",
            "exists": True,
        })
    return rows
```

- [ ] **Step 3: skills_admin.py routes**

Auth：与其他 assistant internal/admin 路由一致（service token / 现有 deps）。响应字段：`skill_id,name,summary,when_to_use,audience,aliases,privacy,pending_hint,doc_files`。  
`GET /skills/{skill_id}`：卡片 + 各 md 正文（`sections: [{name, rel_path, markdown}]`）。  
`GET /skills/help-topics`：解析 `help_topics.yaml`。

- [ ] **Step 4: Register in app.py**

- [ ] **Step 5: Run tests + commit**

```bash
git commit -m "feat: add read-only skills admin API from SkillRegistry files"
```

---

### Task 4: Prompts 只读 API + 写接口 410

**Files:**
- Modify: `assistant_platform/api/prompts.py`
- Modify: `pulse/web/assistant_prompts_api.py`
- Test: `tests/assistant_platform/test_prompts_readonly_api.py`

**Interfaces:**
- `GET /api/assistant/v1/prompts` → `{fragments: [{key, path, description, content_preview}]}`
- `GET /api/assistant/v1/prompts/preview` → `{markdown: str}`
- 所有 POST canary/promote/rollback/fragments/releases → `410`，`detail: "Prompt editing retired; edit files in assistant_platform/prompts/docs"`

- [ ] **Step 1: Failing tests**

```python
def test_prompts_list_from_files(client):
    r = client.get("/api/assistant/v1/prompts")
    assert r.status_code == 200
    keys = {f["key"] for f in r.json()["fragments"]}
    assert "heart.md" in keys

def test_prompt_write_returns_410(client):
    r = client.post("/api/assistant/v1/prompts/fragments", json={"key": "x", "content": "y"})
    assert r.status_code == 410
```

- [ ] **Step 2: Implement** — 读 `loader.load_manifest` + 文件内容；写路由统一 `_gone()`。

- [ ] **Step 3: Pulse 代理同步** — GET 代理新路径；POST 写路径改为本地直接 `HTTPException(410)` 或代理 410。

- [ ] **Step 4: Tests + commit**

```bash
git commit -m "feat: prompt admin read-only APIs; retire write endpoints with 410"
```

---

### Task 5: Pulse 权限 + Skills 代理

**Files:**
- Modify: `pulse/web/permissions.py`
- Create: `pulse/web/assistant_skills_api.py`
- Modify: `pulse/web/app.py`
- Test: `tests/test_permissions_skills_read.py`（或扩现有 permissions 测）

**Interfaces:**
- `assistant:skills:read` ∈ `ALL_PERMISSIONS`；`owner`/`operator` 拥有
- `assistant:prompts:write` / `assistant:prompts:approve` 可保留在 frozenset 但 **不授予** 新语义；`has_permission(..., write)` 对 prompts 写操作一律拒绝（API 已 410）
- Pulse: `GET /api/v2/assistant/skills`, `/skills/{id}`, `/skills/help-topics`

- [ ] **Step 1: Add permission + tests**

```python
def test_owner_has_skills_read():
    # construct Member portal_role=owner
    assert "assistant:skills:read" in resolve_permissions(member)
```

- [ ] **Step 2: Implement proxy**（复制 `assistant_capabilities_api._proxy_assistant` 模式）

- [ ] **Step 3: Register routes in `pulse/web/app.py`**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: proxy skills read APIs and add assistant:skills:read permission"
```

---

### Task 6: web-admin 技能一览 + Prompt 一览 + 菜单改名

**Files:**
- Create: `web-admin/src/views/SkillsView.vue`
- Create: `web-admin/src/views/PromptsView.vue`
- Modify: `web-admin/src/views/CapabilitiesView.vue`（标题/说明文案）
- Modify: `web-admin/src/router/index.ts`
- Modify: `web-admin/src/layouts/MainLayout.vue`
- Delete or gut: `web-admin/src/views/PromptStudioView.vue`（改为 redirect → `/prompts` 或删除后换新组件）

**UI 要求（最小可用）：**

SkillsView：
- 顶栏 Alert：`技能说明书以仓库文件为准，请修改 assistant_platform/skills 后发版。`
- 左 `el-table`/`el-menu` 列出 skills；右 Markdown（可用简单 `<pre>` 或已有 markdown 组件）+ `rel_path`
- `GET /api/v2/assistant/skills` + detail

PromptsView：
- Alert：`人设文案以 assistant_platform/prompts/docs 为准。`
- 片段列表 + preview 接口
- **无** 发布/灰度/回滚/保存按钮

CapabilitiesView：
- 标题改为「工具授权」
- 说明：`此处只控制谁能调用 Capability（Tool），与技能卡片说明书无关。`

Router：
- `/skills` → SkillsView，`permission: assistant:skills:read`
- `/prompts` → PromptsView，`permission: assistant:prompts:read`
- `/prompt-studio` → redirect `/prompts`
- `/capabilities` meta.title → `工具授权`

MainLayout：菜单项「技能一览」「工具授权」「Prompt 一览」

- [ ] **Step 1: 实现页面与路由**

- [ ] **Step 2: 本地 `npm run build`（或项目既有 web-admin 构建命令）确认无 TS 错误**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(web-admin): skills/prompts readonly views; rename capabilities to tool auth"
```

---

### Task 7: 文档与回归

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-admin-skills-tools-readonly-design.md`（状态 → 实现中/已落地）
- Modify: `docs/superpowers/specs/2026-07-20-dingtalk-skills-tools-design.md`（非目标「Skill Studio」旁注：控制台只读已规划）
- Optional: `docs/bot-commands.md` 顶部加一句「管理后台可预览技能，不可在线编辑」

- [ ] **Step 1: 跑回归**

```bash
.venv\Scripts\pytest.exe tests/assistant_platform/test_prompt_compose.py tests/assistant_platform/test_skill_registry.py tests/assistant_platform/test_skills_admin_api.py tests/assistant_platform/test_prompts_readonly_api.py tests/test_capability_handlers_phase_c.py -q
```

Expected: PASS

- [ ] **Step 2: 手工检查清单**

1. 对话仍注入人设（heart/precepts）  
2. `/skills` 能看到 `team.admin`  
3. `/capabilities` 仍能增删 assignment  
4. POST prompt fragment → 410  

- [ ] **Step 3: Commit docs**

```bash
git commit -m "docs: mark admin skills/tools readonly design as implemented"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 文件真源 Prompt | 1–2 |
| 停用 DB release / 停写 | 2 |
| Skills 只读 API | 3、5 |
| Prompts 只读 + 写 410 | 4 |
| 权限 skills:read | 5 |
| 三栏 UI + 工具授权改名 | 6 |
| 迁移文档 / 成功标准 | 7 |
| 非目标（CMS、联动授权、删表） | 不实现 |

## Placeholder / consistency self-review

- 无 TBD；compose 签名统一为可忽略 DB 参数  
- API 路径前缀：Assistant `/api/assistant/v1/*`，Pulse `/api/v2/assistant/*`  
- 页面路由：`/skills`、`/prompts`、`/capabilities`
