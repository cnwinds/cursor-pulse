# Proxy × 官方用量差异 · 调查报告与后续计划

日期：2026-07-23  
样本：熊波 loan → `douw5512@gmail.com`（loan `cee9d54a-…`）  
交互报告：Cursor Canvas `proxy-vs-official-usage-investigation.canvas.tsx`（可在对话旁打开）

## 结论（先看这里）

**不是「协议完全解析不出来」**，而是三类问题叠加：

| # | 问题 | 状态 |
|---|---|---|
| 1 | TurnEnded `field1(Input)` 语义理解错误 → **token 近乎双计（≈2×）** | 已用指纹对齐证实 |
| 2 | 旧 model 抽取接受裸标签 `opus` → **官方无 Opus，实为 `gpt-5.6-sol-max`** | 已用指纹对齐证实；代码 fix 在工作区未部署 |
| 3 | Proxy 用本地价表全价估算，官方用 `chargedCents`（含大量 included=$0）→ **费用差一个数量级** | 已证实（产品口径差 + 被 1/2 放大） |

次要：时区（UTC 日 vs 上海展示）、事件粒度（34 TurnEnded vs 111 Dashboard events）。

## 证据摘要

### 指纹对齐（`cache_read` + `output` + `cache_write` 三字段全等）

- 34 条 proxy 用量中 **26 条** 能对上官方 `usage_records`
- 模型映射：`opus → gpt-5.6-sol-max` ×19；`gpt-5.6-sol-max → gpt-5.6-sol-max` ×7
- Token：匹配子集 proxy 185.4M / 官方 93.0M → **比值 1.99**
- 费用：匹配子集 proxy **$419.81** vs 官方 **$24.75**

### field1 语义（单条验证）

官方事件 `2026-07-22 20:35:00`（`gpt-5.6-sol-max`）：

- `input_no_cache=207`, `cache_write=140243`, `cache_read=10584886`, `output=43745`
- `tokens_total=10,769,081`

对应 proxy `opus @20:55:04`：

- `input=10,725,336` ≈ `207+140243+10584886`
- `cache_read/output/cache_write` 与官方**完全一致**
- 错误公式 `sum(all five)` → `21,511,839` ≈ 官方 ×2

### 官方当日无 Opus

- `usage_daily_aggregates` / `usage_records`：`douw5512` 在 07-22 **零条** `*opus*`
- 费用 $83.46 全部来自 `gpt-5.6-sol-max`（含 INCLUDED / FREE_CREDIT / USAGE_BASED）

## 后续计划

### P0（本周，修正确性）— 已完成

1. **修正 token 汇总与计价输入** ✅
   - `canonical_turn_ended_tokens` / `estimate_cost_cents`（强制 canonical）
   - 单测含指纹样例；本地 DB 已跑 `reprice-proxy`（35 行）

2. **部署 model_tap 修复** ✅（binary 已 build；需重启进程）

3. **UI 文案** ✅「本地估算（非账单）」

4. **全路径审计** ✅ 见 `docs/proxy-usage-model-tap.md`「全项目费用计算路径」
   - 官方 `resolve_cost_fields` / CLI `reprice`：不受影响（字段已 split）
   - `borrowed_cents` / burn_rate：快照差值，非 token 价表
   - 历史回算：`python -m pulse.cli reprice-proxy`

### P1（下周，可观测性与对齐）

4. Debug dump 增加 **响应 TurnEnded** 原始字段（现有 dump 只有请求体 model）
5. 增加「proxy ↔ 官方」指纹对齐脚本/测试（本调查 `.dev/tmp_usage_fingerprint.py` 可产品化）
6. 价表：为 `gpt-5.6-sol*` 增加独立规则；评估 loan「精确消耗」是否改用源账号 sync 的 `chargedCents` 差值

### P2（增强）

7. 按日切分统一时区策略（建议账期用上海，或 UI 同时显示 UTC 日）
8. Model normalize 层：对比工具忽略 `thinking-*` / `cursor-` 前缀差异
9. 中长期：若能拿到正式 protobuf schema，替换启发式 `looksLikeTurnEnded` / `pickSelectedModel`

## 验收标准

- [x] 新入库：`canonical_turn_ended_tokens` 拆 no_cache，指纹样例 total 与官方差仅 reasoning（&lt;5%）
- [x] UI 可见「估算（非账单）」文案（LoansView / ProxyKeysView）
- [x] Python：`tests/test_proxy_service.py` 含 inclusive-input 用例并通过
- [x] 部署产物：已 `go build -o cursor-pulse-proxy.exe`（需**重启**正在跑的 proxy 进程才生效）
- [x] `go test -run 'Model|TurnEnded|FindTurn|LooksLike' ./...` 通过
- [ ] 新流量 `proxy_key_usages.model` 无裸家族标签（重启 proxy 后用真实请求验证）

## 非目标

- 不把 proxy 子集加总当成账号全量账单
- 不在 MITM 路径伪造官方 `kind` / included（账单属性以 Dashboard API 为准）
- 不自动改写历史错误 `model=opus` 行（除非产品明确要求 migration）
