# Admin Console Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge `web-admin` to Pulse / 助手中心 / 系统 navigation; remove obsolete metrics/memory admin pages and their dedicated management APIs.

**Architecture:** Frontend-first menu regroup + route/view deletion; remove Pulse web `/api/memory/*` admin endpoints that only served those pages; keep bot/personamem runtime and `metrics` period APIs. Permission constants `memory:*` / `evolution:run` remain in `ALL_PERMISSIONS` for chat tools, but are dropped from auditor defaults and no longer drive navigation.

**Tech Stack:** Vue 3 + Element Plus, FastAPI, pytest

**Spec:** [2026-07-14-admin-console-cleanup-design.md](../specs/2026-07-14-admin-console-cleanup-design.md)

---

## File map

| Path | Action |
|------|--------|
| `web-admin/src/layouts/MainLayout.vue` | Regroup menu into 3 sections; drop deleted items |
| `web-admin/src/router/index.ts` | Remove 5 routes; dedupe `prompt-studio` |
| `web-admin/src/views/MetricsView.vue` | Delete |
| `web-admin/src/views/MemoryView.vue` | Delete |
| `web-admin/src/views/PrinciplesView.vue` | Delete |
| `web-admin/src/views/DisclosureView.vue` | Delete |
| `web-admin/src/views/EvolutionView.vue` | Delete |
| `web-admin/src/views/UsersView.vue` | Optional: drop memory/evolution from custom-permission checklist labels if listed as admin-only |
| `pulse/web/app.py` | Remove `/api/memory/*` handlers + unused `_memory_svc` / imports |
| `pulse/web/schemas.py` | Remove `PrincipleCreateBody` if unused |
| `pulse/web/permissions.py` | Remove `memory:read` from auditor; update role description |
| `pulse/web/memory_api.py` | Leave file (unused after API removal) — do not delete unless nothing imports it |
| `tests/test_web_memory.py` | Replace memory success tests with 404; keep portal/audit coverage adapted |
| `tests/test_web_auth.py` | Assert auditor no longer has `memory:read` |

---

### Task 1: Backend — remove memory admin APIs (TDD)

**Files:**
- Modify: `pulse/web/app.py`
- Modify: `pulse/web/schemas.py`
- Modify: `tests/test_web_memory.py`
- Modify: `tests/test_web_auth.py`
- Modify: `pulse/web/permissions.py`

- [ ] **Step 1: Rewrite failing/expected tests for removed endpoints**

Replace the memory success tests in `tests/test_web_memory.py` with 404 assertions. Keep `test_portal_grant`. Change `test_audit_logs` so it no longer POSTs principles — assert audit-logs shape with empty or existing actions:

```python
def test_memory_admin_endpoints_removed(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/memory/atoms", headers=headers).status_code == 404
    assert client.get("/api/memory/commitments", headers=headers).status_code == 404
    assert client.get("/api/memory/principles", headers=headers).status_code == 404
    assert client.post(
        "/api/memory/principles",
        headers=headers,
        json={"rule": "x", "tier": "learned"},
    ).status_code == 404
    assert client.get("/api/memory/disclosure", headers=headers).status_code == 404
    assert client.get("/api/memory/evolution", headers=headers).status_code == 404
    assert client.post("/api/memory/evolution/run", headers=headers).status_code == 404


def test_audit_logs(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    res = client.get("/api/audit-logs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "admin_actions" in body
    assert "query_logs" in body
    assert "alerts" in body
```

Delete `test_memory_atoms`, `test_memory_principles`, `test_create_principle`.

In `tests/test_web_auth.py`, update auditor test:

```python
def test_auditor_read_only_write_denied():
    member = Member(
        team_id="t1",
        dingtalk_user_id="u2",
        display_name="Auditor",
        status="active",
        portal_status="active",
        portal_role="auditor",
    )
    assert has_permission(member, "metrics:read")
    assert has_permission(member, "audit:read")
    assert not has_permission(member, "memory:read")
    assert not has_permission(member, "settings:write")
```

- [ ] **Step 2: Run tests — expect failures (endpoints still 200 / auditor still has memory)**

Run:

```bash
pytest tests/test_web_memory.py tests/test_web_auth.py::test_auditor_read_only_write_denied -v
```

Expected: `test_memory_admin_endpoints_removed` FAIL (got 200 not 404); auditor assert may FAIL if `memory:read` still on auditor.

- [ ] **Step 3: Remove endpoints from `pulse/web/app.py`**

Delete the entire block from `@app.get("/api/memory/atoms"...` through the end of `run_evolution` (through `return result` before `@app.get("/api/audit-logs"...`).

Also remove if now unused:

- `def _memory_svc(...):` helper (~line 362)
- imports: `MemoryQueryService`, `PrincipleCreateBody`

Confirm `PrincipleCreateBody` is only used here; if so, delete the class from `pulse/web/schemas.py`.

Do **not** remove `pulse/web/memory_api.py` in this task unless the import graph is clean and you prefer deletion — optional follow-up, not required.

- [ ] **Step 4: Update `pulse/web/permissions.py`**

Remove `"memory:read"` from the `auditor` frozenset. Keep `"memory:read"`, `"memory:write"`, `"evolution:run"` in `ALL_PERMISSIONS` (owner + chat tools).

Update description:

```python
"auditor": "只读访问指标、审计日志与业务只读数据",
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/test_web_memory.py tests/test_web_auth.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pulse/web/app.py pulse/web/schemas.py pulse/web/permissions.py tests/test_web_memory.py tests/test_web_auth.py
git commit -m "$(cat <<'EOF'
fix(admin): remove obsolete memory management APIs

EOF
)"
```

---

### Task 2: Frontend — remove routes and views

**Files:**
- Modify: `web-admin/src/router/index.ts`
- Delete: `web-admin/src/views/MetricsView.vue`
- Delete: `web-admin/src/views/MemoryView.vue`
- Delete: `web-admin/src/views/PrinciplesView.vue`
- Delete: `web-admin/src/views/DisclosureView.vue`
- Delete: `web-admin/src/views/EvolutionView.vue`

- [ ] **Step 1: Edit `web-admin/src/router/index.ts`**

Remove these children entirely:

- `path: 'metrics'` … MetricsView
- `path: 'memory'` … MemoryView
- `path: 'principles'` … PrinciplesView
- `path: 'disclosure'` … DisclosureView
- `path: 'evolution'` … EvolutionView

Keep a **single** `prompt-studio` child (delete the duplicate block that appears twice).

After edit, children under `/` should include: `''`, `accounts`, `quota-board`, `tool-tips`, `access-requests`, `ingestions`, `settings`, `integrations`, `users`, `capabilities`, `sessions`, `prompt-studio` (once), `forbidden`.

- [ ] **Step 2: Delete the five view files**

```bash
rm web-admin/src/views/MetricsView.vue \
   web-admin/src/views/MemoryView.vue \
   web-admin/src/views/PrinciplesView.vue \
   web-admin/src/views/DisclosureView.vue \
   web-admin/src/views/EvolutionView.vue
```

On Windows PowerShell:

```powershell
Remove-Item web-admin/src/views/MetricsView.vue, web-admin/src/views/MemoryView.vue, web-admin/src/views/PrinciplesView.vue, web-admin/src/views/DisclosureView.vue, web-admin/src/views/EvolutionView.vue
```

- [ ] **Step 3: Commit**

```bash
git add web-admin/src/router/index.ts
git add -u web-admin/src/views/
git commit -m "$(cat <<'EOF'
fix(web-admin): drop obsolete metrics and memory views

EOF
)"
```

---

### Task 3: Frontend — regroup MainLayout navigation

**Files:**
- Modify: `web-admin/src/layouts/MainLayout.vue`
- Modify: `web-admin/src/views/UsersView.vue` (permission checklist cleanup)

- [ ] **Step 1: Replace `el-menu` body in `MainLayout.vue`**

Use three `el-sub-menu` groups. Icons remain globally registered via `main.ts`.

```vue
      <el-menu :default-active="active" router>
        <el-sub-menu index="grp-pulse">
          <template #title>
            <span>Pulse</span>
          </template>
          <el-menu-item v-if="auth.hasPermission('settings:read')" index="/">
            <el-icon><Odometer /></el-icon>
            <span>概览</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/accounts">
            <el-icon><Wallet /></el-icon>
            <span>账号台账</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/quota-board">
            <el-icon><TrendCharts /></el-icon>
            <span>额度看板</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('requests:read')" index="/access-requests">
            <el-icon><Tickets /></el-icon>
            <span>工具申请</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('knowledge:read')" index="/tool-tips">
            <el-icon><Reading /></el-icon>
            <span>技巧知识库</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('submissions:read')" index="/ingestions">
            <el-icon><Document /></el-icon>
            <span>摄取记录</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="grp-assistant">
          <template #title>
            <span>助手中心</span>
          </template>
          <el-menu-item v-if="auth.hasPermission('assistant:capabilities:read')" index="/capabilities">
            <el-icon><Grid /></el-icon>
            <span>能力中心</span>
          </el-menu-item>
          <el-menu-item
            v-if="auth.hasPermission('assistant:sessions:read:self') || auth.hasPermission('assistant:sessions:read:all')"
            index="/sessions"
          >
            <el-icon><ChatLineRound /></el-icon>
            <span>会话账本</span>
          </el-menu-item>
          <el-menu-item
            v-if="auth.hasPermission('assistant:prompts:read')"
            index="/prompt-studio"
          >
            <el-icon><EditPen /></el-icon>
            <span>Prompt Studio</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="grp-system">
          <template #title>
            <span>系统</span>
          </template>
          <el-menu-item v-if="auth.hasPermission('audit:read')" index="/audit">
            <el-icon><Notebook /></el-icon>
            <span>审计</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('settings:read')" index="/integrations">
            <el-icon><Connection /></el-icon>
            <span>系统</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('settings:read')" index="/settings">
            <el-icon><Setting /></el-icon>
            <span>配置</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('admin:users')" index="/users">
            <el-icon><Key /></el-icon>
            <span>用户管理</span>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>
```

Add scoped styles so sub-menu titles match the dark aside:

```css
:deep(.el-sub-menu__title) {
  color: #cbd5e1;
}
:deep(.el-sub-menu .el-menu) {
  background: transparent;
}
```

- [ ] **Step 2: Clean `UsersView.vue` custom permission list**

In the array that includes `'memory:read', 'memory:write', 'evolution:run'`, **keep** those strings (chat tools still use `evolution:run`). No change required unless the UI labels imply admin pages — if labels say “记忆后台”, rename to something neutral or leave as capability codes.

- [ ] **Step 3: Manual smoke (dev server if already running)**

Confirm sidebar shows three groups; no 指标/记忆/原则/披露/自进化; Pulse items + 助手中心 + 系统 present; `/prompt-studio` loads once.

- [ ] **Step 4: Commit**

```bash
git add web-admin/src/layouts/MainLayout.vue web-admin/src/views/UsersView.vue
git commit -m "$(cat <<'EOF'
feat(web-admin): regroup console nav into Pulse / assistant / system

EOF
)"
```

---

### Task 4: Verification

**Files:** none new

- [ ] **Step 1: Full relevant pytest**

```bash
pytest tests/test_web_memory.py tests/test_web_auth.py tests/test_web_*.py -q --tb=line
```

Expected: all PASS (or only pre-existing failures unrelated to this change).

- [ ] **Step 2: Spec checklist**

- [ ] Sidebar groups Pulse / 助手中心 / 系统; no memory/principles/disclosure/evolution/metrics
- [ ] 概览、技巧知识库、摄取记录 reachable
- [ ] 能力中心、会话账本、Prompt Studio reachable
- [ ] Duplicate `prompt-studio` route gone
- [ ] Old `/api/memory/*` return 404
- [ ] Pulse accounts/quota/requests unaffected

- [ ] **Step 3: Final commit only if stray fixes remain; otherwise done**

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| Regroup nav into 3 groups | Task 3 |
| Delete metrics/memory/principles/disclosure/evolution UI | Task 2 |
| Dedupe prompt-studio | Task 2 |
| Remove memory admin APIs | Task 1 |
| Keep metrics period API | Task 1 (no touch) |
| Keep bot personamem | Task 1 (no touch) |
| Auditor drop memory:read | Task 1 |
| Keep 概览 / 知识库 / 摄取 | Task 3 |
| Acceptance verification | Task 4 |

No placeholders remaining. Permission constants intentionally retained in `ALL_PERMISSIONS` for chat.
