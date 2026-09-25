# Coding Plan 额度 API（GLM / MiniMax）

Pulse 对 GLM、MiniMax 仅做 **quota-only** 同步（无 usage events）。解析逻辑与 [cc-switch `coding_plan.rs`](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/coding_plan.rs) 对齐；单测 fixtures 见 `tests/fixtures/coding_plan/`。

设计背景与产品边界见 [ADR 0002](./adr/0002-glm-account-quota-integration.md)。

## 智谱 GLM

| 项 | 值 |
|----|-----|
| 路径 | `GET {base}/api/monitor/usage/quota/limit` |
| 国内 base | `https://open.bigmodel.cn`（账号 `api_region=bigmodel`） |
| 国际 base | `https://api.z.ai`（账号 `api_region=zai`） |
| Authorization | **API Key 原文**，不加 `Bearer` |
| 其它头 | `Content-Type: application/json`，`Accept-Language: en-US,en` |

响应要点：

- `success === false` → 同步失败，保留上次快照。
- `data.level` → 套餐档位（lite/pro/max…），同步时可更新台账 plan。
- `data.limits[]`：看 **TOKENS_LIMIT**（及 CREDIT_LIMIT 兼容）；窗口分类 **优先 `unit`**：`3` → 5h，`6` → weekly。
- **TIME_LIMIT**（MCP 等）写入 `quota_extra`，看板可选第三条进度。

实现：`pulse/integrations/coding_plan/zhipu.py`（`fetch_zhipu_quota` / `parse_zhipu_token_tiers`）。

## MiniMax Coding Plan

| 项 | 值 |
|----|-----|
| 路径 | `GET https://{domain}/v1/api/openplatform/coding_plan/remains` |
| 国内 domain | `api.minimaxi.com`（`api_region=cn`） |
| 国际 domain | `api.minimax.io`（`api_region=global`） |
| Authorization | `Bearer <api_key>` |

响应要点：

- `base_resp.status_code === 0` 为成功。
- `model_remains[]` 中取 **`model_name == "general"`**。
- 5h：`current_interval_remaining_percent` → 已用% = `100 - remaining`；`end_time` 为毫秒重置时刻。
- 周桶：仅当 `current_weekly_status == 1` 时展示；`3` 等表示无周限额，**不得**伪造周条。

实现：`pulse/integrations/coding_plan/minimax.py`（`fetch_minimax_quota` / `parse_minimax_tiers`）。

## Pulse 内部

| 能力 | 说明 |
|------|------|
| 同步 | `pulse/ingestion/coding_plan_sync.py`，`sync_kind=coding_plan_quota` |
| 看板 | `GET /api/v2/quota-board?vendor=glm\|minimax` |
| 台账 | `POST /api/v2/accounts`（vendor 分支 + `api_region` + API Key） |
| 快照 | `AccountQuotaSnapshot.quota_extra.tiers`（归一化 `five_hour` / `weekly_limit`） |

**不在范围**：Credential Pool、Key Loan、Cursor MITM；用量分析仅 Cursor 事件聚合。

## 变更风险

上游未文档化字段变更时（参考 cc-switch issue #3036、#3652），应补充 fixture + 单测后再改解析；失败时账号状态 `unknown` / `sync_failed`，scheduler 跳过单账号不阻塞全局。
