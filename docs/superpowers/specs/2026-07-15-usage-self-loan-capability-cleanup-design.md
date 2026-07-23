# 自助用量升级 · 借入 Key 路由 · 能力分配清理

**日期：** 2026-07-15  
**状态：** 已批准并实施
**范围：** 聊天「我的用量」回复形态、借入 Key intent、能力中心重复包/分配清理

## 背景

线上反馈三件事：

1. 能力中心分配表「能力 Key」为 `—`，需确认是否配置错误。
2. 「查下我的用量」只给成员级汇总，未分账号、未按模型、未说明统计周期；期望默认记账周期，可切自然月。
3. 「我借的key」未查借入状态，却回忆「借出的key」并提示发 CSV。

调查结论：

- 分配形态正确（pack 分配时 `capability_key` 为空属预期）；但库内存在同 key 重复包与新旧 assignment 并存。
- 用量走 `usage.query` → 旧 `pulse.query.engine` 的 `period_totals`，与专用「额度」handler 不对齐；数据层已有分账号/日聚合能力。
- 「我借的key」未命中 `key.loan.self.read`（仅精确「我的借用」「借用状态」），落入记忆闲聊 + 固定 CSV CTA。

## 目标

1. **能力分配清理**：每 team 每个 pack key 只保留一份规范包；`team_default` / `role_pack(owner|operator)` 各一份 assignment 指向完整包；seed 幂等且可修复已有脏数据。
2. **「我的用量」升级**：仍用能力 `usage.query`；分账号；默认各账号自己的记账周期并写明区间；可切自然月；每账号下列出**全部**模型（请求数 / tokens / 费用）。
3. **借入 Key 路由**：自然语言「我借的key」等命中 `key.loan.self.read`，不再误入记忆闲聊。

## 非目标

- 不新建 capability key（如 `usage.self.read`）
- 不改「额度」`quota.self.read` 业务逻辑
- 不做成员侧「我借出的 key」查询
- 不改 query engine 的团队 NL（谁最多、排名等）
- 不强制改记忆 responder 的 CSV CTA（可选加固，见下）

## 决策记录

| 项 | 选择 |
|---|---|
| 实施范围 | 三块一起（清理 + 用量 + 借入路由） |
| 多账号默认周期 | 每账号各自记账周期，回复分别写区间 |
| 模型明细 | 每账号下列全部模型（非 Top N） |
| 实现路径 | 方案 1：专用「我的用量」路径，仍映射 `usage.query` |

## 方案概览

采用**方案 1**：

- 「我的用量」类句子 → `usage.query` → 专用自助用量格式化（对齐额度 handler 风格）
- 团队 NL 查询仍走 `pulse.query.engine`
- Intent 扩借入查询；seed/启动清理重复 pack

---

## 1. 能力中心清理

### 现状

- Pack 分配时 UI 显示「能力 Key = —」**正确**（`pack_id` 与 `capability_key` 互斥）。
- 本地库曾出现同 `key` 多包（旧 Phase1 两 key 包 + 完整自助/Owner 包）及多条 assignment。

### 行为

按 `team_id` 在 seed（或显式 cleanup 函数，由 seed 调用）中：

1. 对每个 pack `key`（`cursor_self_service`、`assistant_owner`）：若有多行，选出**规范包**（优先：item 集合覆盖 catalog 目标 keys 最多者；并列则保留已有 assignment 引用最多者 / 创建较早者）。
2. 将该 team 下指向同 key 其它包的 assignment，改为规范包 id；删除重复的 assignment 行（同一 `(scope_type, scope_id)` 只保留一条指向规范包）。
3. 删除无 assignment 引用的重复旧包及其 `pack_items`。
4. 再执行现有 `_get_or_create_pack` / `_get_or_create_pack_item` / `_get_or_create_pack_assignment`，补齐 catalog 中的 keys。

### 验收

- 每 team：每个 pack key 至多一行；`team_default` / `owner` / `operator` 各一条 pack assignment。
- 规范自助包含 `usage.query`、`key.loan.self.read` 等 `SELF_SERVICE_KEYS`；Owner 包含 `OWNER_EXTRA_KEYS` 并集。

---

## 2. 「我的用量」数据流与回复

### 触发与路由

- Intent：现有「我的用量」「查下我的用量」「查询 …」等继续映射 `usage.query`，并解析可选 `period_mode` / `period`。
- 在 Pulse 侧（`channel_command` / `run_command` 或新建专用 handler 仍挂 `usage.query`）识别**自助用量**（文本含「用量」且指向本人，或显式自助句式），走新格式化路径；**不**再进入 `answer_question` 的 `period_totals`。
- 团队 NL（谁最多等）仍走 query engine。

### 账号范围

与额度一致：`ToolCenterRepository.get_primary_accounts_for_member` + `filter_cursor_accounts`。无绑定账号时回复「尚未绑定 Cursor 账号」。

### 数据源

1. 优先：`usage_daily_aggregates`，按 `account_id` + 日期窗口聚合：`event_count`、tokens（input+output+cache_read 或既有字段之和）、`total_cost_usd`，再按 `model` 分组。
2. 若日聚合为空：回退该账号已确认 ingestion 下的 `UsageRecord`，按 `event_date` 过滤后同样按模型汇总。

### 周期

| 模式 | 触发 | 行为 |
|------|------|------|
| `billing_cycle`（默认） | 无特殊词 | 每账号用 `usage_resets_on` + `billing_cycle_containing(today, …)`（或与现有 `billing_cycle_for_period` 对齐的当前周期）；无 `usage_resets_on` 则该账号退回自然月并在文案注明 |
| `calendar_month` | 含「自然月」「本月」，或可解析的 `YYYY-MM` | 所有账号使用同一自然月 `[月初, 下月初)`；首行标明自然月标签 |

回复须写明周期**类型**与**区间**（复用 `format_cycle_range` 风格）。

### 回复形态（示意）

```
你的用量（记账周期，分账号）：

· account@example.com
  周期：2026-06-15 ~ 2026-07-14
  合计：1,200 次，xxx tokens，付费 $0.12
  模型：
  - composer-2：800 次，… tokens，$0.08
  - claude-4：400 次，… tokens，$0.04

· other@example.com
  …
```

自然月模式首行改为「自然月 YYYY-MM」。模型按费用或请求数降序全量列出。末尾可保留「也可发送『额度』查看 Cursor 额度快照」。

### 与现有模块关系

- 复用：`filter_cursor_accounts`、`billing_cycle_*`、`format_cycle_range`；日聚合 / UsageRecord 查询。
- `build_account_usage_summary` 面向计划额度摘要，本路径以「请求/tokens/费用 × 模型」展示为主，可不强依赖其 quota 字段。

---

## 3. 借入 Key 路由

### Intent

在 `match_capability_intent` 中，于「申请借 Key」判断之前：

- 命中 `key.loan.self.read`：规范化后匹配「我的借用」「借用状态」，或含「我借的」「借入的」「借的key」等，且**不含**「借出」。
- 「借key / 申请key / …」仍走 `key.loan.request`。
- 「借用」「借用列表」仍走管理员 `key.loan.list`。

### 执行

不改 `KeyLoanService.active_loan_for_borrower` 业务；仅保证路由到达现有 `_handle_key_loan_commands` 借入查询分支。

### 可选加固

若记忆路径召回内容含借/key 语义，避免套用固定「请发 CSV」CTA。非硬性验收项。

---

## 4. 测试与验收

### 单测建议

- Intent：`我借的key` / `借入的key` → `key.loan.self.read`；`借出的key` 不命中 self.read；`借key` 仍为 request。
- Intent/解析：`查下我的用量` → `usage.query` + 默认 `billing_cycle`；含「自然月」或 `2026-06` → `calendar_month`。
- 用量格式化：多账号不同 `usage_resets_on` 时区间不同；模型全列出；无账号文案。
- Seed 去重：构造双包双 assignment 后 seed，只剩规范包与单 assignment。

### 验收清单

- [ ] 「我的用量」分账号、写明周期类型与区间、下列全部模型（次数 / tokens / 费用）
- [ ] 默认记账周期；「自然月」或 `YYYY-MM` 可切换
- [ ] 无 `usage_resets_on` 时退回自然月并注明
- [ ] 「我借的key」→ 借入状态，不再出现「借出」记忆 + CSV
- [ ] 「我的借用」仍可用；「借key」仍走申请
- [ ] 能力分配无重复旧包；解析含 `usage.query` / `key.loan.self.read`
- [ ] 相关单测通过

## 风险

- 日聚合未覆盖的账号会回退 UsageRecord，跨月记账周期需正确加载多月 ingestion。
- 模型极多时钉钉消息变长（已接受全量列出）。
- 清理脚本误删：仅删「同 key 且无 assignment」的重复包；先改 assignment 再删。
