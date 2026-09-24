# ADR 0002：Coding Plan 账号（GLM / MiniMax）台账与额度看板

- **状态**：草案（待产品确认）
- **日期**：2026-09-24（修订：纳入 MiniMax、对齐 cc-switch）
- **参考实现**：[farion1231/cc-switch](https://github.com/farion1231/cc-switch) `src-tauri/src/services/coding_plan.rs`（Token Plan 额度查询，含单元测试与解析边界）
- **范围**：Pulse Web 账号台账、额度看板、后台 **quota-only** 同步；**不包含** MITM 代理、Key 借用、用量事件/历史用量

## 背景与目标

Pulse 已管理 **Cursor** 账号：台账、API Key、`CursorSyncService`（周期 Plan & Usage + usage events + `UsageSummary`）、额度看板（Total / Auto / API）。

现需在同一套 **AiVendor / AiAccount / 凭证 / AccountQuotaSnapshot** 模型下，增加两家 **Coding Plan** 供应商：

| 供应商 | slug | 能力边界 |
|--------|------|----------|
| 智谱 GLM（Z.ai / bigmodel.cn） | `glm` | **仅有** monitor 额度接口 |
| MiniMax Coding Plan | `minimax` | **仅有** remains 额度接口 |

**关键约束（与 cc-switch 一致）**：GLM、MiniMax **均无已核实的「历史用量 / usage events」公开 API**。Pulse 对这两家的同步 **只能** 周期性拉取 **当前窗口剩余/已用百分比**，不能像 Cursor 一样填充「用量分析」、按模型拆账、`UsageDailyAggregate` 等。产品预期需在 UI 与文档中写清：**看板 = 实时额度快照；无历史曲线**。

**明确不在首期范围**：

- 开放平台按量充值余额（与 Coding Plan 窗口额度不同体系）。
- Kimi / 火山 / ZenMux 等 cc-switch 已支持的其他 Token Plan（可后续按同一框架扩展）。
- 智谱 **团队版**（`?type=2` + `bigmodel-organization` / `bigmodel-project` 头，见 cc-switch `query_zhipu_team`）——建议 **M2** 单独账号类型或扩展字段。
- Credential Pool / Key Loan / Auto Lender / Jev / Go proxy（仍 **仅 Cursor**）。

## 外部接口（以 cc-switch 为准）

### 智谱 GLM

```http
GET {base}/api/monitor/usage/quota/limit
Authorization: <api_key>          # 不加 Bearer（cc-switch 实测）
Content-Type: application/json
Accept-Language: en-US,en
```

| 站点 | base（由账号 `api_region` 决定，**不做跨站 fallback**） |
|------|--------------------------------------------------------|
| 国内 | `https://open.bigmodel.cn` |
| 国际 | `https://api.z.ai` |

响应：`success`、`data.level`（lite/pro/max…）、`data.limits[]`：

- 看板主用 **`TOKENS_LIMIT`**（兼容 `CREDIT_LIMIT` 大小写不敏感）。
- 窗口分类 **必须优先看 `unit`**（cc-switch issue #3036）：`unit=3` → 5h，`unit=6` → weekly；**不能**仅按 `nextResetTime` 排序（周期末 weekly 可能比 5h 更早重置）。
- `TIME_LIMIT`：MCP 等次数额度，**可选**第三进度条；不参与 Cursor 式 auto/api 映射。
- 老套餐可能只有 1 条 `TOKENS_LIMIT` → 只展示 5h 桶。

### MiniMax

```http
GET https://{domain}/v1/api/openplatform/coding_plan/remains
Authorization: Bearer <api_key>
Content-Type: application/json
```

| 区域 | domain（cc-switch：额度接口固定在此，与国内推理域名 `api.minimax.cn` 分离） |
|------|-------------------------------------------------------------------------------|
| 国内 | `api.minimaxi.com` |
| 国际 | `api.minimax.io` |

响应：`base_resp.status_code === 0`，`model_remains[]` 中取 **`model_name == "general"`**（跳过 video 等）：

| 字段 | 含义 |
|------|------|
| `current_interval_remaining_percent` | 5h 桶 **剩余%** → 已用% = `100 - remaining` |
| `end_time` | 5h 重置时刻（毫秒） |
| `current_weekly_status == 1` | 周桶 **激活**；`3` 等表示无周限额，**不得**展示假周桶（cc-switch 单测覆盖） |
| `current_weekly_remaining_percent` | 周桶剩余% |
| `weekly_end_time` | 周重置时刻 |

> MiniMax 接口曾变更（cc-switch issue #3652）；实现应 **防御式解析** + fixture 回归，与 upstream 保持跟进。

## 现状调研摘要（Pulse 仓库）

| 层级 | Cursor 现状 | Coding Plan 所需变更 |
|------|-------------|----------------------|
| 模型 | `AiVendor` / `AiPlan` / `AiAccount` 已多厂家 | seed `glm`、`minimax` + 套餐 |
| 快照 | `AccountQuotaSnapshot`：周期 + total/auto/api_pct + 美分 | Cursor 不变；CP 用 `quota_extra.tiers` |
| 同步 | `CursorSyncService`：events + snapshot | 新增 **quota-only** 路径，**不写** `UsageSummary` |
| `sync_tick` | 遍历 credential，一律 `CursorSyncService` | 按 `account.vendor.slug` 分发 |
| 台账 | 创建强制 `crsr_`；凭证「仅 Cursor」 | vendor 分支 + Key 规则 |
| 看板 | `vendor_slug="cursor"` | 纳入 glm/minimax；`display_mode` 分支渲染 |
| 用量分析 | Cursor 事件驱动 | GLM/MiniMax 账号 **隐藏或空态**（无数据属预期） |

## 设计原则

1. **一套台账，三种同步语义**：`cursor_full`（额度+事件）、`coding_plan_quota`（仅额度）。
2. **归一化 tier 模型**：与 cc-switch 的 `QuotaTier` 对齐（`five_hour`、`weekly_limit`），便于共用看板组件与后续接 Kimi。
3. **解析逻辑可移植**：Python 实现应对照 cc-switch 单测用例（智谱 unit 分类、MiniMax weekly_status、general 筛选等）。
4. **无历史则不伪造**：不写入假 `UsageSummary`；burn 预测对 CP 仅在有 `resets_at` + 多快照时可做简化，**首期可只展示百分比+倒计时**。
5. **容错**：未文档化接口 → 解析失败 `unknown`、保留上次成功快照、scheduler 不拖死。

## 统一数据模型

### 1. 厂家与套餐（seed）

| slug | name |
|------|------|
| `glm` | GLM（智谱 / Z.ai） |
| `minimax` | MiniMax |

**GLM 套餐**（slug 对齐 API `data.level`，同步时可更新，见开放问题）：

`lite` / `pro` / `max`（及 API 将来新增 level → 落 `plan_name` + slug 规范化）

**MiniMax 套餐**：

首期 API **不返回明确 plan 档位**（cc-switch `credential_message` 为 None）→ seed 单一占位 plan，如 `coding_plan` / 「Coding Plan」，或让用户选手填备注；若后续响应带 plan 字段再对齐。

`billing_type`：`coding_plan_window`（与 Cursor 的 `fixed_monthly_pool` 区分，便于报表过滤）。

### 2. 账号扩展

| 字段 | 适用 | 说明 |
|------|------|------|
| `api_region` | glm | `zai` \| `bigmodel`（必填其一；**不做 auto 跨站探测**，与 cc-switch 路由一致） |
| `api_region` | minimax | `cn` \| `global` → 选 `minimaxi.com` / `minimax.io` |

`account_identifier`：用户填写邮箱/备注（CP key **无** Cursor 式 JWT 邮箱解析）。

### 3. 快照：`quota_extra`（Coding Plan 共用）

`AccountQuotaSnapshot` 增加（迁移）：

```text
sync_kind        VARCHAR(16)   -- 'cursor' | 'coding_plan'（默认 cursor）
quota_extra      JSON          -- coding_plan 时必填；cursor 为 null
```

**Cursor 行**：仍写 `cycle_*`、`total/auto/api_pct`、`limit/used/remaining_cents`，`quota_extra = null`。

**Coding Plan 行** `quota_extra`（schema_version: 1）：

```json
{
  "schema_version": 1,
  "plan_level": "pro",
  "tiers": [
    {
      "name": "five_hour",
      "utilization_pct": 12.0,
      "resets_at": "2026-09-24T15:00:00Z"
    },
    {
      "name": "weekly_limit",
      "utilization_pct": 31.0,
      "resets_at": "2026-09-28T00:00:00Z"
    }
  ],
  "extras": [
    {
      "name": "mcp_time_limit",
      "utilization_pct": 7.0,
      "resets_at": null
    }
  ],
  "raw_limit_count": 3
}
```

**写入 `AccountQuotaSnapshot` 标量列（便于排序 / 复用预警）**：

- `total_pct` ← 所有 tier 中 **max(utilization_pct)**（最紧窗口）。
- `cycle_end` / `cycle_end_at` ← 所有 tier 中 **最早**的 `resets_at`（「即将重置」）；无 reset 则留空。
- `auto_pct` / `api_pct` / `*_cents` → **null**（看板勿当 Cursor 解释）。

可选：保留 `captured_at` 作为「最后同步」；CP **无** Cursor 式 billing cycle 起止。

### 4. 凭证

同 `AiAccountCredential`；`sync_enabled` 默认 true。Key 校验：

- Cursor：仍 `crsr_`
- GLM / MiniMax：非空 + 长度上限；**不**硬编码前缀（除非产品确认）

## 后端模块划分

```
pulse/integrations/coding_plan/
  zhipu.py      # parse_zhipu_token_tiers, fetch (port from cc-switch)
  minimax.py    # parse_minimax_tiers, fetch
  types.py      # CodingPlanQuota, QuotaTier
pulse/ingestion/
  coding_plan_sync.py   # CodingPlanQuotaSyncService
  sync_dispatch.py      # vendor → CursorSyncService | CodingPlanQuotaSyncService
```

**`CodingPlanQuotaSyncService.sync_account`**：

1. 解密 primary credential。
2. 按 vendor 调 zhipu / minimax client。
3. 解析 tiers → 写 `AccountQuotaSnapshot` + 更新 `usage_resets_on`（weekly tier 的 date，若有）。
4. **跳过** `UsageIngestionService`、`_recompute_account_summaries`、Key Loan expire。
5. 返回 `IngestionResult(event_count=0)`。

**`sync_tick`**：credential 查询不变；内部分发。配置：`coding_plan_sync.enabled`（或复用 `cursor_sync.enabled` 的总开关 + 分 vendor 标志，待实现时二选一）。

**错误通道**（对齐 cc-switch 注释）：

- 网络/读体中断 → 抛错/重试，**保留上次快照**。
- 401/业务错误 → `last_sync_status=failed`，看板 `sync_blocker`。

## API 变更

| 端点 | 变更 |
|------|------|
| `POST /api/v2/accounts` | 必填 `vendor_id`；Cursor 逻辑不变；glm/minimax：region + key + identifier，创建后触发 quota sync |
| `GET /api/v2/accounts` | glm/minimax 返回 `credential` 摘要（同 Cursor 权限模型） |
| 凭证 bind/sync | 按 vendor 分发 |
| `GET /api/v2/quota/board` | 活跃账号：`cursor` + `glm` + `minimax`；项增加 `vendor_slug`、`display_mode`、`quota_tiers` |

Board item 示例（Coding Plan）：

```json
{
  "vendor_slug": "glm",
  "display_mode": "coding_plan_tiers",
  "plan_name": "Pro",
  "total_pct": 31,
  "quota_tiers": [
    { "label": "5 小时", "name": "five_hour", "utilization_pct": 12, "resets_at": "..." },
    { "label": "每周", "name": "weekly_limit", "utilization_pct": 31, "resets_at": "..." }
  ],
  "has_usage_detail": false
}
```

`has_usage_detail: false` → 前端隐藏「本周期用量明细」折叠区（Cursor 专用 `cursor_pools`）。

## 前端

### 账号台账

- 标题：**AI 账号台账**（Cursor + GLM + MiniMax）。
- 筛选：全部 / Cursor / GLM / MiniMax；列 **平台**。
- 新建：选平台 → region（GLM/MiniMax）→ 套餐（GLM 可选，MiniMax 占位）→ Key → 标识。

### 额度看板

| display_mode | UI |
|--------------|-----|
| `cursor`（默认） | 现有 Total / Auto / API + 用量明细 |
| `coding_plan_tiers` | 5h + 每周 进度条；GLM 可选 MCP 条；**无** 美元 API 剩余文案 |
| 共用 | 状态 tag（healthy/warning/exhausted）、重置倒计时、手动「同步」 |

- 隐藏：Jev、Auto Lender 标签（CP 账号）。
- 副标题：说明 CP 与官方 Coding Plan 窗口对齐，**无历史用量**。

### 用量分析 / 概览

- 对 `vendor.slug in (glm, minimax)`：**不展示**或展示「该平台暂无历史用量同步」——避免用户以为坏了。

## 权限、审计、文档

- 权限：沿用 `accounts:read/write`。
- 审计：create/bind/sync 带 `vendor_slug`。
- 新增 `docs/coding-plan-quota-apis.md`：端点、auth、cc-switch 引用、变更风险。

## 测试计划

| 类型 | 内容 |
|------|------|
| 单元 | **移植** cc-switch 中 `parse_zhipu_token_tiers` / `parse_minimax_tiers` 对应用例（fixtures JSON） |
| 集成 | mock HTTP：创建 glm/minimax 账号 → snapshot → board API |
| 回归 | Cursor 全链路不变 |

Fixtures 目录建议：

- `tests/fixtures/coding_plan/zhipu_*.json`
- `tests/fixtures/coding_plan/minimax_*.json`

## 迁移与 rollout

1. DB：`api_region`、`sync_kind`、`quota_extra`。
2. Seed：glm + minimax vendors/plans。
3. 部署说明：现有 Cursor 账号零影响。

## 开放问题（需确认）

1. **GLM 套餐**：`level` 与台账 plan 不一致 → 自动改 plan + history，还是仅展示 API level？
2. **MiniMax 套餐**：是否接受单一占位 plan + 备注，直到 API 提供档位？
3. **看板布局**：Cursor / CP **混排** vs **分 Tab**？默认排序是否 Cursor 优先？
4. **预警阈值**：CP 是否沿用 95%/100%（基于 max tier utilization）？
5. **智谱团队版**：M1 做不做（需 org/project 字段）？
6. **Burn 预测**：CP 无事件，是否 **不做**「预计耗尽日期」（首期建议不做）？
7. **边界声明**：是否在 `CONTEXT.md` 写明 CP 账号永不进 proxy/借用池？

## 实施阶段

| 阶段 | 交付 |
|------|------|
| **M1** | seed + zhipu/minimax client（对照 cc-switch）+ quota sync + 台账 + 看板 tier UI + 用量分析空态 |
| **M2** | 智谱团队版、scheduler 指标、plan 自动对齐策略、`docs/coding-plan-quota-apis.md` |
| **M3** | Kimi 等同框架扩展；助手/bot 查额度（可选） |

---

**请确认开放问题后进入 M1。** 实现时以 cc-switch `coding_plan.rs` 为解析真源，避免重复踩坑（智谱 unit、MiniMax weekly_status、GLM 无 Bearer）。
