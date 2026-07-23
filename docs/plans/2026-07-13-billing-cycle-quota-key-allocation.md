# 计费周期额度看板 + Key 调配 Implementation Plan

> **For agentic workers:** 用 checkbox (`- [ ]`) 逐任务实现。本计划为一次性全量实现（额度看板 + K2 一键分配），非分期。

**Goal:** 解决"每个账号计费重置周期不一致，却按自然月统计"的别扭，并支持把有富余额度账号的 Cursor Key 调配给已用完额度的人。

**核心决策（已与需求方确认）：**

| 维度 | 决策 |
|------|------|
| 统计/UI 组织 | **S2 + S3**：`账号台账页`保留自然月对账；**新增`额度看板页`**，以各账号自身计费周期为准，含"周期进度 + 燃尽预测" |
| 计费周期来源 | **自动**：同步时从 Cursor API `billingCycleEnd` 写入/校准 `usage_resets_on`，保留手工覆盖 |
| Key 调配 | **K2 一键分配**：系统推荐最优借出账号 → `CreateUserApiKey` → 借用记录 → 撤销 |
| 借用人标识 | **系统已有成员**（`ai_account_members` / 用户），可追溯 |
| 回收策略 | **人工点撤销**（默认） **＋ 借出账号到达重置日自动 revoke**（可为每笔借用设置） |
| 用量归因 | **接受近似**：借出 = 共享借出账号剩余额度；按账号用量差值(baseline vs 当前)近似归因，无法按 key 精确拆分 |

**Architecture:** 在现有 Python 单体 + Vue3 admin 上扩展。新增 `account_quota_snapshots`、`key_loans` 表；放开每账号单 key 约束；`cursor_api.py` 补 Key 管理 RPC；`sync.py` 写周期与额度快照；新增 `burn_rate` 预测模块与 `quota_api`；前端新增额度看板页。

**Tech Stack:** Python 3.11+, SQLAlchemy 2, FastAPI, Vue 3 + TS + Element Plus, pytest

**关键约束（务必周知）:**
- Cursor 用量事件只标记 `owningUser`，**不区分 Key**。借出 key 的消耗会累加到借出账号计费周期，无法按 key 精确拆分 → 归因用"发放时 baseline 用量 vs 当前用量"差值近似。
- Key 管理 RPC (`Create/List/RevokeUserApiKey`) 为**非官方**接口，须保留人工 Dashboard 兜底路径。
- 创建返回的完整 key **只出现一次**，须一次性展示 + 立即加密入库。

**Spec 参考:** [Cursor 用量 API](../cursor-usage-api.md) · [AI 工具中心 v2](./2026-07-01-ai-tool-center-v2.md)

---

## 文件地图

| 路径 | 改动 |
|------|------|
| `pulse/storage/models.py` | 新增 `AccountQuotaSnapshot`、`KeyLoan`；`AiAccountCredential` 加字段并去唯一约束；`AiAccount` 加 `resets_on_source` |
| `pulse/storage/migrate.py` | 新表 + 补列（沿用 `_ensure_columns` 风格） |
| `pulse/integrations/cursor_api.py` | 新增 `create_user_api_key` / `list_user_api_keys` / `revoke_user_api_key` |
| `pulse/ingestion/sync.py` | 同步时写 `usage_resets_on` + `account_quota_snapshots` |
| `pulse/tool_center/burn_rate.py` | **新**：周期进度、额度进度、预计耗尽日、紧张度、借出源推荐 |
| `pulse/tool_center/key_loans.py` | **新**：借用记录 CRUD、baseline 快照、近似归因计算 |
| `pulse/web/quota_api.py` | **新**：额度看板、推荐、一键分配、撤销 REST |
| `pulse/web/app.py` | 挂载 `quota_api` 路由 |
| `web-admin/src/views/QuotaBoardView.vue` | **新**：额度看板页（卡片 + 进度条 + 预测 + 分配弹窗） |
| `web-admin/src/router/index.ts` | 新增 `/quota-board` |
| `web-admin/src/layouts/MainLayout.vue` | 侧栏加"额度看板" |
| `tests/test_burn_rate.py` / `tests/test_key_loans.py` / `tests/test_quota_api.py` | **新** 测试 |

---

## Phase 1 — 数据模型与迁移

### Task 1: ORM 模型
- [x] `AccountQuotaSnapshot`：`id, account_id(FK), captured_at, cycle_start, cycle_end, limit_cents, used_cents, remaining_cents, auto_pct, api_pct, total_pct`；索引 `(account_id, captured_at)`
- [x] `KeyLoan`：`id, source_account_id(FK), credential_id(FK), borrower_member_id(FK, 可空), borrower_note, baseline_used_cents, created_at, revoked_at, status(active/revoked/expired), auto_revoke_on_reset(bool), note`
- [x] `AiAccountCredential`：去掉 `uq_credential_account`；新增 `key_role`(primary/loan, 默认 primary)、`display_name`、`remote_key_id`(Cursor 整数 id)、`assignee_member_id`、`status`(active/revoked, 默认 active)
- [x] `AiAccount`：新增 `resets_on_source`(manual/api, 默认 manual) 用于标记 `usage_resets_on` 是否被手工锁定

### Task 2: 迁移
- [x] `migrate.py` 补 `AiAccountCredential`/`AiAccount` 新列；`create_all` 建两张新表
- [x] 既有单 key 数据 `key_role` 回填为 `primary`
- [x] 验证旧库升级不丢数据（`data/pulse.db`）

---

## Phase 2 — Cursor API 扩展

### Task 3: Key 管理 RPC
- [x] `create_user_api_key(token, name) -> {api_key}`（`CreateUserApiKey`）
- [x] `list_user_api_keys(token) -> [{id, masked_key, name, created_at}]`（`ListUserApiKeys`）
- [x] `revoke_user_api_key(token, key_id) -> None`（`RevokeUserApiKey`，整数 id）
- [x] 复用现有 `_post` + accessToken 缓存；401 重新 exchange
- [x] 单测（mock HTTP）：成功/失败/token 过期重试

---

## Phase 3 — 同步增强

### Task 4: 自动写计费周期 + 额度快照
- [x] `sync.py`：同步成功后将 `period_usage["billingCycleEnd"]`(ms→date) 写 `usage_resets_on`，仅当 `resets_on_source != 'manual-locked'`（手工填写视为锁定，不被覆盖）
- [x] 写一条 `AccountQuotaSnapshot`：`planUsage.{limit,remaining,totalSpend,autoPercentUsed,apiPercentUsed,totalPercentUsed}` + `billingCycleStart/End`
- [x] 无 `planUsage`（如智谱等非 Cursor）时跳过快照，不报错
- [x] 单测：给定 mock `period_usage` → 断言 `usage_resets_on` 与快照落库

---

## Phase 4 — 燃尽预测与借用逻辑

### Task 5: `burn_rate.py`
- [x] `cycle_progress(snapshot, today) = (today-cycle_start)/(cycle_end-cycle_start)`
- [x] `quota_progress(snapshot) = used_cents/limit_cents`
- [x] `projected_exhaustion_date` = 按当前燃烧速率外推；判断是否早于 `cycle_end`
- [x] `status`：healthy / warning(额度进度显著超周期进度) / exhausted；阈值可配
- [x] `recommend_lenders(snapshots)`：候选借出账号排序键 = 剩余额度降序 × 预计不耗尽 × 距重置天数远优先
- [x] 单测覆盖：正常/超速/已耗尽/临近重置

### Task 6: `key_loans.py`
- [x] `create_loan(source_account, borrower_member_id, ...)`：记录 `baseline_used_cents`(当前快照 used) + `auto_revoke_on_reset`
- [x] `revoke_loan(loan_id)`：置 revoked、算 `borrowed_cents ≈ 当前 used - baseline`
- [x] `list_active_loans()` / 按账号/按借用人查询
- [x] `expire_loans_on_reset()`：借出账号到达 `usage_resets_on` 且 `auto_revoke_on_reset=True` → 调 revoke（供调度调用）
- [x] 单测

---

## Phase 5 — 后端 REST

### Task 7: `quota_api.py`
- [x] `GET /api/v2/quota-board`：各账号最新快照 + 周期进度/额度进度/预计耗尽/紧张度，按紧张度排序
- [x] `GET /api/v2/quota-board/recommend`：返回排序后的可借出账号
- [x] `POST /api/v2/accounts/{id}/loan-key`：换 token → `create_user_api_key` → 加密入库(loan 角色, 关联 assignee) → 写 `key_loans` → **一次性返回明文 key**
- [x] `POST /api/v2/loans/{id}/revoke`：`revoke_user_api_key` → 更新 loan + credential.status → 返回近似 `borrowed_cents`
- [x] `GET /api/v2/loans`：借用记录列表
- [x] `app.py` 挂载路由；鉴权沿用现有 admin 中间件
- [x] 单测（含明文 key 仅返回一次、撤销幂等）

---

## Phase 6 — 前端

### Task 8: 额度看板页
- [x] `QuotaBoardView.vue`：账号卡片，双进度条(周期进度/额度进度)、预计耗尽日、剩余额度、紧张度标签，按紧张度排序，可筛厂家
- [x] "为某人分配 key" 弹窗：选借用人(成员) → 展示 recommend 推荐借出账号 → 确认 → 调 loan-key → **明文 key 一次性展示 + 复制**（关闭后不可再看）
- [x] 借用记录区：活跃借用列表 + "撤销"按钮（确认后调 revoke，展示近似消耗）
- [x] `router/index.ts` 加 `/quota-board`；`MainLayout.vue` 侧栏加"额度看板"
- [x] `AccountsView.vue` 保持自然月不变

### Task 9: 调度接线（自动回收）
- [x] 现有每日调度中加 `expire_loans_on_reset()` 调用（借出账号重置日自动 revoke 到期借用）

---

## 验收标准

- [x] 额度看板按各账号真实计费周期展示，跨账号可按"谁快用完"排序
- [x] 同步后 `usage_resets_on` 自动准确，手工填写不被覆盖
- [x] 对已耗尽成员可一键生成借出 key，明文仅现一次，记录可查
- [x] 人工撤销即时失效；借出账号重置日到期借用自动撤销（如已设）
- [x] 撤销后展示近似借用消耗
- [x] 账号台账页自然月对账行为不变
- [x] 新增单测全绿，旧库迁移无损

## 风险与兜底

- 非官方 Key RPC 变更风险 → 失败时提示改用 Dashboard 人工建 key/撤销，UI 保留人工录入 loan 入口
- 归因不精确 → UI 明确标注"借用消耗为账号用量差值近似，非精确按 key"
- 借出把源账号拖爆 → recommend 排除预计将耗尽账号；看板对借出源做超速预警
