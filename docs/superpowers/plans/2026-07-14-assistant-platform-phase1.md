# Assistant Platform Phase 1 — 能力中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 Capability Registry（定义 / 能力包 / 分层分配 / 风险闸门）、Pulse internal Provider API，以及三个首批能力 `quota.self.read` / `cursor.key.bind` / `guide_image.update`；旧钉钉命令经 feature flag 兼容桥走同一执行链；后台可查看目录并做分配。

**Architecture:** Pulse 实现可信 Provider（`/api/internal/v1/capabilities`），自行二次校验 actor 与业务规则。Assistant 拥有 Registry 与 `tool_invocations` 审计，通过 HTTP 调用 Pulse。钉钉旧命令默认仍走 legacy；每个能力独立 `CAPABILITY_BRIDGE_<KEY>=true` 时改走 `Assistant CapabilityExecutor → Pulse Provider`。本阶段**不**接管会话主路由、不注入 LLM tools（留给阶段 2）。

**Tech Stack:** Python 3.11+, SQLAlchemy 2, FastAPI, httpx, Vue 3 + Element Plus（web-admin）, pytest

**Spec:** [2026-07-14-assistant-platform-design.md](../specs/2026-07-14-assistant-platform-design.md) §7、§15.2–15.3、§16.3、§20 阶段 1、§21 能力平台

**Depends on:** Phase 0（`assistant_platform` 旁路、ingest、`contracts/provider.py`）

**Out of scope（禁止混入）:** 会话接管、personamem 迁移、Prompt Studio、评审/进化、LLM tool schema 注入、任意新能力（除三首批）

---

## 文件地图

| 路径 | 职责 |
|------|------|
| `pulse/capabilities/__init__.py` | Pulse Provider 包 |
| `pulse/capabilities/manifest.py` | 三能力 manifest 定义（key/version/schema/risk） |
| `pulse/capabilities/handlers/quota_self_read.py` | 本人额度只读 |
| `pulse/capabilities/handlers/cursor_key_bind.py` | 绑定 Cursor Key |
| `pulse/capabilities/handlers/guide_image_update.py` | 更新引导图 |
| `pulse/capabilities/invoke.py` | 路由 + 幂等 + 二次鉴权 |
| `pulse/web/internal_capabilities_api.py` | `/api/internal/v1/capabilities/*` |
| `pulse/config.py` | `CapabilityBridgeConfig` + Pulse internal token |
| `assistant_platform/capabilities/models.py` | Registry ORM（definitions/packs/assignments/invocations） |
| `assistant_platform/capabilities/seed.py` | 种子三能力 + 默认包 |
| `assistant_platform/capabilities/resolve.py` | 分层分配解析 |
| `assistant_platform/capabilities/executor.py` | 确认闸门 + 调 Pulse Provider |
| `assistant_platform/capabilities/pulse_client.py` | httpx 客户端 |
| `assistant_platform/secrets/store.py` | Phase 1 最小 Secret Store（AES-GCM，仅 bind） |
| `assistant_platform/api/capabilities.py` | Assistant 对外 invoke / resolve / admin CRUD |
| `pulse/channels/capability_bridge.py` | 钉钉命令 → Assistant invoke |
| `pulse/channels/commands.py` / `handler.py` | feature flag 分支 |
| `pulse/web/assistant_capabilities_api.py` | Portal 代理：目录与分配（JWT） |
| `web-admin/src/views/CapabilitiesView.vue` | 能力中心页 |
| `tests/pulse/test_capability_*.py` / `tests/assistant_platform/test_capability_*.py` | TDD |

---

## 首批能力契约（固定）

| key | version | risk | confirmation | 参数要点 | Provider 复用 |
|-----|---------|------|--------------|----------|---------------|
| `quota.self.read` | `1` | `read` | 无 | `{}` 或 `{period?}` | primary accounts + latest `AccountQuotaSnapshot` + `analyze_burn_rate` |
| `cursor.key.bind` | `1` | `sensitive` | 发起人确认（桥接下命令即确认） | `{email?, api_key}` 或 `{secret_ref}` | `resolve_bind_cursor_account` + `CredentialService.bind_cursor_api_key` + sync |
| `guide_image.update` | `1` | `destructive` | 确认；需 owner/operator 或钉钉 admin 映射 | `{image_base64}` 或 `{image_path}` | `save_guide_image_override` |

默认能力包：

- `cursor_self_service`：`quota.self.read` + `cursor.key.bind`
- `assistant_owner`：上述 + `guide_image.update`

团队默认：全员 `cursor_self_service`；`guide_image.update` 仅 owner 包 / 显式 allow。

---

### Task 1: Pulse Capability Manifest + invoke 骨架

**Files:**
- Create: `pulse/capabilities/__init__.py`
- Create: `pulse/capabilities/manifest.py`
- Create: `pulse/capabilities/invoke.py`
- Create: `tests/test_capability_manifest.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_capability_manifest.py
from pulse.capabilities.manifest import get_manifest, list_operations


def test_manifest_contains_three_phase1_ops():
    keys = {op["capability_key"] for op in list_operations()}
    assert keys >= {"quota.self.read", "cursor.key.bind", "guide_image.update"}


def test_get_manifest_quota_self_read():
    op = get_manifest("quota.self.read", "1")
    assert op["risk_level"] == "read"
    assert op["status"] == "active"
```

- [ ] **Step 2: 实现 manifest**

每个 op 为 dict，至少含：`capability_key`, `capability_version`, `display_name`, `description`, `risk_level`, `input_schema`, `output_schema`, `idempotency_required`, `status`, `timeout_seconds`。

- [ ] **Step 3: invoke 骨架**

```python
# pulse/capabilities/invoke.py
def invoke_capability(session, *, request: CapabilityInvokeRequest, config) -> CapabilityInvokeResult:
    op = get_manifest(request.capability_key, request.capability_version)
    if op is None or op["status"] != "active":
        return CapabilityInvokeResult(status="failed", error_code="unknown_capability", user_message="未知或不活跃能力")
    # handlers 在后续 Task 注册；未知 handler → failed
```

复用 `assistant_platform.contracts.provider` 的 dataclass（Pulse 可 import 同仓契约；勿复制第二份）。

- [ ] **Step 4: 测试通过并 commit**

```bash
git add pulse/capabilities tests/test_capability_manifest.py
git commit -m "feat(pulse): add capability provider manifest skeleton"
```

---

### Task 2: `quota.self.read` handler

**Files:**
- Create: `pulse/capabilities/handlers/__init__.py`
- Create: `pulse/capabilities/handlers/quota_self_read.py`
- Create: `tests/test_capability_quota_self_read.py`
- Modify: `pulse/capabilities/invoke.py`（注册 handler）

- [ ] **Step 1: 失败测试**

用临时 DB fixture（参考 `tests/conftest.py`）：创建 team、member、Cursor 主账号、一条 `AccountQuotaSnapshot`，调用 handler，断言：

- `status == "succeeded"`
- `result` 含本人账号，不含他人账号
- `user_message` 非空可读

另测：无主账号 → `succeeded` 或 `failed` 带明确文案（推荐 succeeded +「尚未绑定 Cursor 账号」）。

- [ ] **Step 2: 实现**

```python
def handle_quota_self_read(session, *, team_id, actor_member_id, arguments, config) -> CapabilityInvokeResult:
    # 1. load Member by id；校验 team
    # 2. get_primary_accounts_for_member(...)
    # 3. 对每个 Cursor 账号取 latest snapshot（KeyLoanService / repository 已有模式）
    # 4. analyze_burn_rate(snapshot) 可选增强文案
    # 5. 组装 result + user_message
```

**禁止**返回全团队 quota-board 数据。

- [ ] **Step 3: commit**

```bash
git commit -m "feat(pulse): implement quota.self.read capability handler"
```

---

### Task 3: `cursor.key.bind` handler

**Files:**
- Create: `pulse/capabilities/handlers/cursor_key_bind.py`
- Create: `tests/test_capability_cursor_key_bind.py`
- Modify: `pulse/capabilities/invoke.py`

- [ ] **Step 1: 失败测试**

- 合法 actor + 有效 key mock（patch `CursorClient` / sync）→ succeeded，credential 落库加密
- 非本人且非 admin → failed `forbidden`
- arguments 缺 key → failed `invalid_arguments`
- 断言日志/返回中**无**完整 `crsr_` 明文（可断言 `user_message` 仅含 hint）

- [ ] **Step 2: 实现**

复用：

- `resolve_bind_cursor_account`
- `CredentialService.bind_cursor_api_key`
- `CursorSyncService.sync_account`（失败时 status 仍可为 succeeded 但 result 标注 sync_warning，或 failed retryable——选一种并写进测试；推荐：绑定成功但 sync 失败 → `succeeded` + `result.sync_ok=false`）

`confirmed_by` 为空时：Phase 1 Provider **拒绝** sensitive 调用（`error_code=confirmation_required`）。命令桥在调用前把 `confirmed_by=actor_member_id`。

- [ ] **Step 3: commit**

```bash
git commit -m "feat(pulse): implement cursor.key.bind capability handler"
```

---

### Task 4: `guide_image.update` handler

**Files:**
- Create: `pulse/capabilities/handlers/guide_image_update.py`
- Create: `tests/test_capability_guide_image_update.py`

- [ ] **Step 1: 失败测试**

- owner/operator portal_role 或钉钉 admin 映射成员 → 可写覆盖图到 tmp `raw_files_dir`
- 普通成员 → `forbidden`
- `confirmed_by` 为空 → `confirmation_required`
- 非法图片字节 → `invalid_arguments`

- [ ] **Step 2: 实现**

复用 `pulse.channels.dingtalk.guide_image.save_guide_image_override`。权限：`member.portal_role in ("owner","operator")` **或** `is_dingtalk_admin(member.dingtalk_user_id, config.admin.dingtalk_user_ids)`（为 Task 11 统一权限做过渡）。

- [ ] **Step 3: commit**

```bash
git commit -m "feat(pulse): implement guide_image.update capability handler"
```

---

### Task 5: Pulse internal Provider HTTP API

**Files:**
- Create: `pulse/web/internal_capabilities_api.py`
- Modify: `pulse/web/app.py`（register）
- Modify: `pulse/config.py`：`internal_service_token: str = ""`（env `PULSE_INTERNAL_SERVICE_TOKEN`）
- Create: `tests/test_internal_capabilities_api.py`

- [ ] **Step 1: 路由**

```
GET  /api/internal/v1/capabilities/manifest
POST /api/internal/v1/capabilities/invoke
GET  /api/internal/v1/capabilities/invocations/{invocation_id}  # Phase 1：若无异步，可返回 404 或仅内存/DB 最近记录桩
```

鉴权：`Authorization: Bearer <PULSE_INTERNAL_SERVICE_TOKEN>` 或 `X-Pulse-Internal-Token`。未配置 token 时**拒绝所有** internal 调用（与 Assistant 开发便利相反——Provider 默认闭）。

- [ ] **Step 2: POST body** 对齐 `CapabilityInvokeRequest` 字段；响应对齐 `CapabilityInvokeResult`（可用 pydantic model）。

- [ ] **Step 3: 幂等**

Pulse 侧表 `pulse_capability_invocations`（或复用简单表）：唯一 `(team_id, idempotency_key)`，重复返回首次结果。

- [ ] **Step 4: TestClient 测试 + commit**

```bash
git commit -m "feat(pulse): expose internal capability provider HTTP API"
```

---

### Task 6: Assistant Registry ORM + seed

**Files:**
- Create: `assistant_platform/capabilities/__init__.py`
- Create: `assistant_platform/capabilities/models.py`
- Create: `assistant_platform/capabilities/seed.py`
- Modify: `assistant_platform/storage/db.py` 或 `models.py` 确保 `create_all` 包含新表
- Create: `tests/assistant_platform/test_capability_registry_seed.py`

表（前缀 `ap_`）：

- `ap_capability_definitions`：key, display_name, status, ...
- `ap_capability_versions`：definition_id, version, risk_level, input_schema_json, provider_type=`pulse_http`, provider_operation, prompt_instruction, status
- `ap_capability_packs`：key, display_name, team_id
- `ap_capability_pack_items`：pack_id, capability_key, capability_version
- `ap_capability_assignments`：team_id, scope_type=`team_default|role_pack|user_allow|user_deny`, scope_id, pack_id 或 capability_key, ...
- `ap_tool_invocations`：invocation_id, capability_key, actor..., status, request/result redacted json, ...

- [ ] **Step 1: seed**

`seed_phase1_capabilities(session, team_id)`：写入三定义/版本、两包、团队默认 `cursor_self_service`、owner 角色包含 guide。

- [ ] **Step 2: init 时调用 seed（幂等）**

- [ ] **Step 3: commit**

```bash
git commit -m "feat(assistant): add capability registry schema and phase1 seed"
```

---

### Task 7: 分配解析 `resolve_capabilities`

**Files:**
- Create: `assistant_platform/capabilities/resolve.py`
- Create: `tests/assistant_platform/test_capability_resolve.py`

- [ ] **Step 1: 测试矩阵**

| 场景 | 期望 |
|------|------|
| 默认成员 | 含 quota + bind，不含 guide |
| owner 角色包 | 含 guide |
| user_deny bind | 无 bind |
| user_allow guide（非 owner） | 有 guide |
| definition disabled | 不出现 |
| 渠道不支持（若字段存在） | 过滤 |

解析顺序严格按规格 §7.3。

返回结构建议：

```python
@dataclass
class ResolvedCapability:
    key: str
    version: str
    risk_level: str
    # ... prompt_instruction, input_schema 等供后续阶段 LLM 使用
```

- [ ] **Step 2: 实现 + commit**

```bash
git commit -m "feat(assistant): resolve capability grants by team role and user exceptions"
```

---

### Task 8: Assistant Executor + Pulse client

**Files:**
- Create: `assistant_platform/capabilities/pulse_client.py`
- Create: `assistant_platform/capabilities/executor.py`
- Create: `assistant_platform/secrets/store.py`（最小）
- Create: `tests/assistant_platform/test_capability_executor.py`
- Modify: `assistant_platform/config.py`：`pulse_base_url`, `pulse_internal_token`

- [ ] **Step 1: Secret Store（仅 bind）**

```python
def put_secret(session, *, kind: str, plaintext: str) -> str:  # returns ref_id
def get_secret(session, ref_id: str) -> str | None:
```

加密 key：`ASSISTANT_SECRET_KEY` 或复用派生自 `ASSISTANT_SERVICE_TOKEN` 的 blake2（文档写明生产必须独立 key）。表 `ap_secrets`：ref_id, kind, ciphertext, created_at；**禁止**写入 chat 原文表。

- [ ] **Step 2: Executor**

```python
class CapabilityExecutor:
    def invoke(self, *, actor_member_id, team_id, capability_key, arguments, confirmed: bool) -> CapabilityInvokeResult:
        # 1. resolve：无授权 → failed forbidden（且不调 Pulse）
        # 2. risk gate：sensitive/destructive 且 not confirmed → awaiting 语义用 failed confirmation_required（Phase 1 无会话状态机也可）
        # 3. bind：若 arguments 含 api_key，先 put_secret，改传 secret_ref；调 Pulse 时再取出放入 Provider arguments（TLS+service token）；Pulse 响应后可删 secret（可选）
        # 4. 写 ap_tool_invocations
        # 5. pulse_client.invoke(...)
```

- [ ] **Step 3: 用 httpx mock Pulse → 测试授权拒绝 / 确认拒绝 / 成功转发**

- [ ] **Step 4: commit**

```bash
git commit -m "feat(assistant): execute authorized capabilities via pulse provider"
```

---

### Task 9: Assistant HTTP API（invoke / me / admin list）

**Files:**
- Create: `assistant_platform/api/capabilities.py`
- Modify: `assistant_platform/api/app.py` 挂载路由
- Create: `tests/assistant_platform/test_capability_api.py`

路由：

```
POST /api/assistant/v1/capabilities/invoke
GET  /api/assistant/v1/capabilities/me?member_id=&team_id=&channel=dingtalk
GET  /api/assistant/v1/capabilities/catalog
POST /api/assistant/v1/capabilities/assignments   # admin：user_allow/deny 或挂 pack
DELETE /api/assistant/v1/capabilities/assignments/{id}
```

鉴权：现有 `service_token`（Pulse bot / Pulse admin proxy 调用）。Phase 1 **不**直接接浏览器 JWT。

- [ ] **Step 1: 测试 invoke 与 me**

- [ ] **Step 2: commit**

```bash
git commit -m "feat(assistant): expose capability invoke and assignment APIs"
```

---

### Task 10: 钉钉命令兼容桥 + feature flags

**Files:**
- Create: `pulse/channels/capability_bridge.py`
- Modify: `pulse/config.py`：`CapabilityBridgeConfig` 字段
  - `quota_self_read: bool = False`
  - `cursor_key_bind: bool = False`
  - `guide_image_update: bool = False`
  - env：`CAPABILITY_BRIDGE_QUOTA_SELF_READ` 等
- Modify: `pulse/channels/commands.py`：`我的` 可走额度能力（注意：今日「我的」=提交记录；桥开启后 **新增** 命令 `额度`/`我的额度` 映射 `quota.self.read`，避免破坏「我的」语义；或文档明确「我的」改为额度——**推荐新命令 `额度`**，旧「我的」不变）
- Modify: `handle_bind_cursor_command`：flag 开则桥接
- Modify: `handler.py` 设置引导图两步流：flag 开则确认后调 bridge（图片字节 → invoke）
- Create: `tests/test_capability_bridge.py`
- Modify: `.env.example`

- [ ] **Step 1: bridge 客户端**

```python
def invoke_via_assistant(*, config, team_id, member_id, capability_key, arguments, confirmed=True) -> str:
    # POST assistant /capabilities/invoke with service token
    # return user_message；fail_open：异常则抛给调用方决定是否回退 legacy
```

命令侧策略：bridge 失败时 **回退 legacy**（与 mirror fail-open 一致），并打 error 日志。

- [ ] **Step 2: 测试 flag 关=legacy；flag 开=mock assistant**

- [ ] **Step 3: commit**

```bash
git commit -m "feat(bot): bridge selected commands to assistant capabilities behind flags"
```

---

### Task 11: 统一权限来源（钉钉 admin ↔ portal）

**Files:**
- Modify: `pulse/channels/admin_gate.py` 或新建 `pulse/authz/actor.py`
- Create: `tests/test_actor_authz.py`
- Modify: guide handler / capability handler 共用

规则（Phase 1）：

```python
def can_manage_guide_image(member, config) -> bool:
    if member.portal_role in ("owner", "operator"):
        return True
    return is_dingtalk_admin(member.dingtalk_user_id, config.admin.dingtalk_user_ids)
```

`guide_image.update` Provider 与 bot legacy 路径都改用此函数。文档注明：长期以 portal 角色 + 能力分配为准，钉钉 admin 列表仅兼容。

- [ ] **Step 1: 测试 + commit**

```bash
git commit -m "fix: unify guide-image authz across portal roles and dingtalk admins"
```

---

### Task 12: Portal 代理 API + web-admin 能力中心页

**Files:**
- Create: `pulse/web/assistant_capabilities_api.py`
- Modify: `pulse/web/app.py`
- Modify: `pulse/web/permissions.py`：新增 `assistant:capabilities:read` / `assistant:capabilities:write`（owner/operator 默认拥有）
- Create: `web-admin/src/views/CapabilitiesView.vue`
- Modify: `web-admin/src/router/index.ts`
- Modify: `web-admin/src/layouts/MainLayout.vue`
- Create: `tests/test_assistant_capabilities_api.py`

Portal 路由（JWT）：

```
GET  /api/v2/assistant/capabilities/catalog
GET  /api/v2/assistant/capabilities/assignments
POST /api/v2/assistant/capabilities/assignments
DELETE /api/v2/assistant/capabilities/assignments/{id}
GET  /api/v2/assistant/capabilities/members/{member_id}/resolved
```

实现：httpx 转发到 Assistant，附带 `X-Assistant-Token` + actor 元数据。

UI（克制）：

- Tab1：能力目录（只读表：key、version、risk、status）
- Tab2：分配（列表 + 表单：成员/角色 + allow/deny/pack）
- Tab3：当前用户 resolved 预览（选成员）

无花哨仪表盘。

- [ ] **Step 1: API 测试**

- [ ] **Step 2: 前端最小可用 + commit**

```bash
git commit -m "feat(admin): capability catalog and assignment UI via portal proxy"
```

---

### Task 13: Phase 1 验收

**Files:**
- Modify: `README.md`（能力桥 flag + Provider token 说明）
- Modify: spec 状态行标注 Phase 1 计划链接

- [ ] **Step 1: 跑测试**

```bash
pytest tests/test_capability_manifest.py tests/test_capability_quota_self_read.py tests/test_capability_cursor_key_bind.py tests/test_capability_guide_image_update.py tests/test_internal_capabilities_api.py tests/test_capability_bridge.py tests/test_actor_authz.py tests/test_assistant_capabilities_api.py tests/assistant_platform/test_capability_*.py -v
```

- [ ] **Step 2: 手动冒烟清单（写入 README）**

1. 启动 Pulse web + Assistant（双边 token 配齐）
2. `GET` Pulse manifest → 三能力
3. 后台打开能力中心，给用户 deny `cursor.key.bind`，resolved 立即无 bind
4. `CAPABILITY_BRIDGE_QUOTA_SELF_READ=true`，钉钉发「额度」→ 走能力链
5. flag 关闭 → 旧行为

- [ ] **Step 3: commit**

```bash
git commit -m "docs: document assistant platform phase 1 capability center"
```

---

## Phase 1 完成定义

- [ ] Pulse `/api/internal/v1/capabilities/manifest|invoke` 可用，三 handler 绿
- [ ] Assistant Registry seed + resolve 分层分配正确
- [ ] Executor 未授权不调 Pulse；sensitive 无确认拒绝
- [ ] Secret Store 不把 Key 明文写入会话/审计正文
- [ ] 三能力 feature flag 桥可独立开关，失败回退 legacy
- [ ] 引导图权限 portal ∪ 钉钉 admin
- [ ] web-admin 能力目录与分配可用
- [ ] 相关 pytest 全绿

## 计划自检（对照规格阶段 1）

| 规格要求 | 任务 |
|----------|------|
| Capability Registry / 包 / 分配 / 风险闸门 | Task 6–8 |
| Pulse internal Provider API | Task 1–5 |
| 三首批能力 | Task 2–4 |
| 后台目录与分配 | Task 12 |
| 统一 portal 与钉钉权限 | Task 11 |
| 旧命令兼容桥 + 独立 flag | Task 10 |
| 不混入会话接管 / Prompt / 进化 | Out of scope |

## 下一步（不在本计划）

阶段 2：Assistant 接管钉钉/Web 主路由与完整会话账本；LLM 仅见 resolved 能力 Schema。
