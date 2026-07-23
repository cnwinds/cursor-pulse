# 文件即 Skill + 向量路由名片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 废除 `catalog.yaml`；每个 `docs/**/*.md` 成为独立 Skill（frontmatter=名片）；每轮用向量检索注入命中名片（0 命中不注入）；`load_skill_docs` 只读单文件；文档变更按 hash 自动重建索引。

**Architecture:** `SkillRegistry` 改为扫盘构建卡片字典；新增 `ap_skill_embeddings` + `SkillVectorIndex`（复用现有 Embedder）；`orchestrator` 在组 system 前调用 `route_skill_cards(query, actor)`；后台/API 按文件级 skill 列表展示。

**Tech Stack:** Python、SQLAlchemy、现有 `assistant_platform.memory.embedding`、pytest、Vue 技能一览页。

**Spec:** [2026-07-21-file-as-skill-vector-routing-design.md](../specs/2026-07-21-file-as-skill-vector-routing-design.md)

---

## File map

| 文件 | 职责 |
|------|------|
| `assistant_platform/skills/models.py` | `SkillCard` 保持；确认字段够用 |
| `assistant_platform/skills/registry.py` | 扫盘、单文件 `load_docs`、`get_card`、嵌入文本 |
| `assistant_platform/skills/vector_index.py` | **新建**：索引同步、检索 top‑k |
| `assistant_platform/storage/models.py` | **新增** `SkillEmbeddingRow` |
| `assistant_platform/config.py` | `SkillsVectorConfig` |
| `assistant_platform/skills/agent_tools.py` | `section` deprecate（忽略） |
| `assistant_platform/skills/formatting.py` | 空卡片友好文案 |
| `assistant_platform/conversation/orchestrator.py` | 向量路由注入 |
| `assistant_platform/conversation/agent_policy.py` | 空卡片 / 文件级 skill_id 说明 |
| `assistant_platform/api/skills_admin.py` | 文件级列表与详情 |
| `web-admin/src/views/SkillsView.vue` | 左文件列表 / 右单文件 |
| `assistant_platform/skills/docs/**/*.md` | 补 `name`/`summary` |
| `assistant_platform/skills/help_topics.yaml` | skill_id 改文件级 |
| `assistant_platform/skills/catalog.yaml` | **删除**（P0 末） |
| 测试 | registry / vector / orchestrator / admin / help |

---

### Task 1: Frontmatter → SkillCard 扫盘（无 catalog）

**Files:**
- Modify: `assistant_platform/skills/registry.py`
- Modify: `assistant_platform/skills/models.py`（若需 `rel_path` 字段则加；否则用 skill_id 推导）
- Test: `tests/assistant_platform/test_skill_registry.py`
- Create fixtures under `tests/assistant_platform/fixtures/skills_docs/`（可选；优先用真实 docs）

- [x] **Step 1: 改写失败测试（文件级 id）**

将 `test_list_cards_filters_admin_skill` 等改为断言文件级 id，例如：

```python
def test_list_cards_filters_admin_skill_files():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    admin = SkillActorContext(
        "m1", "owner", frozenset({"usage.aggregate", "quota.self.read"})
    )
    member_ids = {c.skill_id for c in reg.list_cards(member)}
    admin_ids = {c.skill_id for c in reg.list_cards(admin)}
    assert "team.admin/overview" not in member_ids
    assert "team.admin/tasks/aggregate" not in member_ids
    assert "team.admin/overview" in admin_ids or any(
        i.startswith("team.admin/") for i in admin_ids
    )
    assert "cursor.self/tasks/quota" in member_ids or "cursor.self/overview" in member_ids
```

`pending_hint`：断言命中带 `pending_hint: true` 的文件（迁移后为 `cursor.onboarding/manager.md` 或 overview——Task 6 补 frontmatter 时写明；本任务先在目标文件加 `pending_hint: true`）。

- [x] **Step 2: 跑测试确认失败**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skill_registry.py -q --tb=line
```

Expected: FAIL（仍从 catalog 读旧 id）

- [x] **Step 3: 实现扫盘 Registry**

核心行为：

```python
def _scan_docs(self) -> dict[str, SkillCard]:
    cards: dict[str, SkillCard] = {}
    for path in sorted(self._docs_root.rglob("*.md")):
        rel = path.relative_to(self._docs_root).as_posix()  # e.g. cursor.self/tasks/quota.md
        meta, body = self._parse_frontmatter(path)
        skill_id = str(meta.get("skill_id") or "").strip() or rel.removesuffix(".md")
        name = str(meta.get("name") or "").strip() or self._first_heading(body) or skill_id
        summary = str(meta.get("summary") or "").strip() or (
            self._when_to_use_items(meta)[:1][0] if self._when_to_use_items(meta) else name
        )
        cards[skill_id] = SkillCard(
            skill_id=skill_id,
            name=name,
            summary=summary,
            when_to_use=tuple(self._when_to_use_items(meta)),
            audience=frozenset(str(x) for x in (meta.get("audience") or ["member"])),
            aliases=tuple(str(x).strip() for x in (meta.get("aliases") or []) if str(x).strip()),
            privacy=(str(meta["privacy"]).strip() if meta.get("privacy") else None),
            pending_hint=bool(meta.get("pending_hint", False)),
        )
    return cards
```

- `__init__`：`self._cards = self._scan_docs()`，**不再**读 `catalog.yaml`
- `load_docs(skill_id, *, section=..., actor, token_budget)`：
  - 忽略 `section`
  - 用 `skill_id` 找到文件（`docs/{skill_id}.md`）；校验 card audience
  - 渲染单文件 `_render_doc_section`
- `list_doc_files` / admin：改为列出全部文件；`skill_id` 即文件 id；详情读单文件（见 Task 7）
- 删除 `_load_catalog` / `_resolve_doc_paths` 多文件合并逻辑

- [x] **Step 4: 跑 registry 测试至通过**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skill_registry.py tests/assistant_platform/test_skill_help_render.py tests/assistant_platform/test_skill_agent_integration.py -q --tb=short
```

同步修这些测试里的旧 `skill_id` / `section=all` 假设。

- [x] **Step 5: Commit**

```bash
git add assistant_platform/skills/registry.py assistant_platform/skills/models.py tests/assistant_platform/test_skill_*.py
git commit -m "$(cat <<'EOF'
refactor(skills): scan docs as file-level skills, drop catalog load

EOF
)"
```

---

### Task 2: 文档 frontmatter 补齐 + 删除 catalog.yaml

**Files:**
- Modify: `assistant_platform/skills/docs/**/*.md`（每个文件补 `name`/`summary`；审批文件加 `pending_hint`）
- Delete: `assistant_platform/skills/catalog.yaml`
- Modify: `assistant_platform/skills/help_topics.yaml`（可与 Task 7 一起；本任务至少改指向文件级 id）

- [x] **Step 1: 批量为每个 md 补 name/summary**

规则：
- `name` ← 现有 `#` 标题
- `summary` ← 一句话（可从原 catalog 对应条目抄）
- `cursor.onboarding/manager.md`：`pending_hint: true`，`audience: [manager, admin]`
- 域 `overview.md`：收窄 `when_to_use` 为「总览/入口」，避免抢走具体 task 的语义

- [x] **Step 2: 更新 help_topics.yaml 的 skill_id**

示例映射：

| topic_key | 新 skill_id |
|-----------|-------------|
| my | `cursor.self/tasks/my` |
| my_usage | `cursor.self/tasks/my-usage` |
| quota | `cursor.self/tasks/quota` |
| bind | `cursor.self/tasks/bind-key` |
| borrow | `key.loan/tasks/borrow` |
| report | `team.admin/tasks/report` |
| … | 其余同理指向具体文件 |

- [x] **Step 3: 删除 catalog.yaml；全库 grep 清引用**

```bash
rg "catalog\.yaml" -g "*.py" -g "*.md" -g "*.yaml" -g "*.vue"
```

- [x] **Step 4: 跑技能相关测试**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skill_registry.py tests/assistant_platform/test_skills_admin_api.py tests/assistant_platform/test_help_filter.py -q --tb=short
```

- [x] **Step 5: Commit**

```bash
git add assistant_platform/skills/docs assistant_platform/skills/help_topics.yaml
git add -u assistant_platform/skills/catalog.yaml
git commit -m "$(cat <<'EOF'
chore(skills): file-level frontmatter cards; remove catalog.yaml

EOF
)"
```

---

### Task 3: `load_skill_docs` 单文件 + policy / formatting

**Files:**
- Modify: `assistant_platform/skills/agent_tools.py`
- Modify: `assistant_platform/conversation/agent_policy.py`
- Modify: `assistant_platform/skills/formatting.py`
- Test: `tests/assistant_platform/test_skill_agent_integration.py`、`test_agent_policy.py`

- [x] **Step 1: 更新 tool schema**

`section` 保留参数但 description 改为「已废弃，忽略」；`invoke_load_skill_docs` 不再把 section 传给合并逻辑（registry 已忽略）。

- [x] **Step 2: formatting 空列表**

```python
def format_skill_cards_block(cards: list[SkillCard]) -> str:
    if not cards:
        return (
            "## 可用技能\n\n"
            "（本轮未匹配到专项技能名片；可正常陪聊。"
            "若用户提出明确业务请求，再根据需要调用 load_skill_docs。）"
        )
    # ... existing card formatting ...
```

- [x] **Step 3: policy**  
说明 skill_id 为文件路径形（如 `cursor.self/tasks/quota`）；有名片时再 load。

- [x] **Step 4: 测试 + Commit**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skill_agent_integration.py tests/assistant_platform/test_agent_policy.py -q --tb=short
git commit -m "feat(skills): single-file load_skill_docs and empty-card policy"
```

---

### Task 4: Skill 向量表 + Index 同步/检索

**Files:**
- Create: `assistant_platform/skills/vector_index.py`
- Modify: `assistant_platform/storage/models.py`（`SkillEmbeddingRow`）
- Modify: `assistant_platform/config.py`（`SkillsVectorConfig`）
- Test: `tests/assistant_platform/test_skill_vector_index.py`

- [x] **Step 1: 写失败测试（HashingEmbedder）**

```python
def test_sync_and_route_returns_matching_skill(tmp_path, session_factory):
    # 写入 2 个 md：quota / chatty
    # sync_index
    # route("我的额度还剩多少", actor) → 含 cursor.self/tasks/quota
    # route("今天天气怎么样", actor) → []（threshold 够高时）
```

- [x] **Step 2: 模型**

```python
class SkillEmbeddingRow(Base):
    __tablename__ = "ap_skill_embeddings"
    skill_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    rel_path: Mapped[str] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64))
    audience_json: Mapped[list] = mapped_column(JSON, default=list)
    embedding_json: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

确保 `init_assistant_db` / `create_all` 包含该表。

- [x] **Step 3: SkillsVectorConfig**

```python
class SkillsVectorConfig(BaseModel):
    enabled: bool = True
    score_threshold: float = 0.35  # HashingEmbedder 下单测可调低；生产用真实 embedder 再标定
    top_k: int = 3
    resync_interval_seconds: int = 45
```

挂到 `AssistantConfig.skills_vector`；环境变量前缀如 `ASSISTANT_SKILLS_VECTOR_*`。

- [x] **Step 4: SkillVectorIndex API**

```python
class SkillVectorIndex:
    def __init__(self, *, registry: SkillRegistry, embedder: Embedder, session_factory, config: SkillsVectorConfig): ...
    def sync(self) -> None:  # hash diff → embed → upsert/delete
    def route_cards(self, query: str, actor: SkillActorContext) -> list[SkillCard]: ...
```

`embed_text_for_skill(card, body)`：name/id/summary/when_to_use/aliases + body（截断）。  
检索：cosine；过滤 audience；`score >= threshold`；`top_k`。  
embed 失败：log + 跳过该文件。

- [x] **Step 5: 测试通过 + Commit**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skill_vector_index.py -q --tb=short
git commit -m "feat(skills): vector index table and route_cards"
```

---

### Task 5: Orchestrator 接入路由 + 启动/周期 sync

**Files:**
- Modify: `assistant_platform/conversation/orchestrator.py`
- Modify: assistant 启动入口（查找 `create_assistant_app` / worker 启动处，挂 `SkillVectorIndex.sync` + 后台 timer）
- Test: `tests/assistant_platform/test_orchestrator_agent.py` 或新建 `test_skill_routing_orchestrator.py`（mock index）

- [x] **Step 1: orchestrator**

替换：

```python
skill_cards = skill_registry.list_cards(skill_actor)
```

为：

```python
if config.skills_vector.enabled and skill_vector_index is not None:
    try:
        skill_cards = skill_vector_index.route_cards(text, skill_actor)
    except Exception:
        logger.exception("skill vector route failed; injecting no cards")
        skill_cards = []
else:
    skill_cards = []  # 与 0 命中一致；打 warning 一次
    logger.warning("skills vector disabled or unavailable; no skill cards injected")
```

`text` 为本轮用户消息。不要全量 `list_cards` 回退。

- [x] **Step 2: 启动时 sync；后台按 `resync_interval_seconds` 再 sync**

单测可不启 timer；集成用手动 `sync()`。

- [x] **Step 3: 测试：mock route 返回 1 张卡，断言 system 含该 name；route 空则 system 走空卡片文案**

- [x] **Step 4: Commit**

```bash
git commit -m "feat(skills): wire vector card routing into orchestrator"
```

---

### Task 6: 管理后台 API + SkillsView 文件级

**Files:**
- Modify: `assistant_platform/api/skills_admin.py`
- Modify: `pulse/web/assistant_skills_api.py`（若仅代理则可能不用改）
- Modify: `web-admin/src/views/SkillsView.vue`
- Test: `tests/assistant_platform/test_skills_admin_api.py`、`tests/test_assistant_skills_api.py`

- [x] **Step 1: API**

`GET /skills` → `list_all_cards()`（每个文件一张卡）+ `rel_path`  
`GET /skills/{skill_id}` → 单卡 + 单文件 `markdown`（skill_id 含 `/`，注意 FastAPI 路径：用 `path` 转换或 query；**推荐** `skill_id: path` → `/skills/{skill_id:path}`）

- [x] **Step 2: Vue**  
左边直接 `skills` 文件列表；右边展示选中文件正文（沿用现解析 frontmatter 逻辑）。去掉「技能包下多 section 文件树」若已是单文件则可简化为单栏内容。

- [x] **Step 3: 测试 + Commit**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skills_admin_api.py tests/test_assistant_skills_api.py -q --tb=short
git commit -m "feat(admin): file-level skills list and detail API/UI"
```

---

### Task 7: 回归与设计文档收尾

**Files:**
- Modify: 旧 specs 中「catalog / 服务域 skill」表述加指向新设计的链接
- Run: 扩大 pytest 范围

- [x] **Step 1: 全量相关测试**

```bash
.\.venv\Scripts\python.exe -m pytest tests/assistant_platform/test_skill_registry.py tests/assistant_platform/test_skill_vector_index.py tests/assistant_platform/test_skill_agent_integration.py tests/assistant_platform/test_skill_help_render.py tests/assistant_platform/test_skills_admin_api.py tests/assistant_platform/test_agent_policy.py tests/assistant_platform/test_help_filter.py tests/assistant_platform/test_orchestrator_agent.py tests/test_assistant_skills_api.py -q --tb=short
```

结果：52 passed，无 skip。

- [ ] **Step 2: 手工检查清单**（留待人工/集成环境验证）

- 问「我的额度」→ 日志有 route hit → 名片 → 可 load → `quota_self_read`
- 「你好」→ 无名片 → 陪聊
- 改 md 后等 resync → 新说法可检索
- 后台技能一览按文件展示

- [x] **Step 3: Commit docs**

```bash
git commit -m "docs: point skill specs to file-as-skill vector routing"
```

---

## Spec coverage check

| Spec 要求 | Task |
|-----------|------|
| 废除 catalog，一文件一 skill | 1–2 |
| frontmatter 名片 | 1–2 |
| load_skill_docs 单文件，section 忽略 | 1, 3 |
| 向量索引 + hash 自动重建 | 4–5 |
| 0 命中不注入 | 3, 5 |
| orchestrator 路由 | 5 |
| help_topics / 后台 | 2, 6 |
| Tool 授权不变 | 全程不改 invoke |

## Default config note

- 开发无真实 embedding 时：可用 `HashingEmbedder`，threshold 在单测中单独设定；生产用与 archive 相同的 OpenAI 兼容 embedder，上线前用 10～20 条真实问法标定 `score_threshold`。
