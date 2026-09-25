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

### 智谱团队版（M2）

| 项 | 值 |
|----|-----|
| URL | `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit?type=2`（**仅国内**，无 z.ai 团队档） |
| Authorization | 与个人版相同（Key 原文，无 Bearer） |
| 额外头 | `bigmodel-organization`、`bigmodel-project`（与 API Key 三者缺一不可） |
| 响应 | 与个人版相同 → 复用 `parse_zhipu_token_tiers` |

Pulse 台账字段：`glm_organization_id`、`glm_project_id`（均非空时走团队查询）；创建时 `api_region` 固定为 `bigmodel`。

实现：`fetch_zhipu_team_quota` / `fetch_glm_quota`（按账号是否配置 org/project 自动分支）。

同步时若 API `data.level` 与台账 plan slug 不一致，会调用 `change_account_plan` 并写入 `AiAccountPlanHistory`（note 含 `GLM API level=…`）。

## Kimi Coding Plan（M3）

| 项 | 值 |
|----|-----|
| 路径 | `GET https://api.kimi.com/coding/v1/usages` |
| Authorization | `Bearer <api_key>` |
| 区域 | 无分站（`api_region` 留空） |

响应要点（cc-switch `query_kimi`）：

- `limits[].detail`：`limit` / `remaining` / `resetTime` → **5h** 桶（已用% = `(limit-remaining)/limit`）
- `usage`：总体 **周** 限额，映射 `weekly_limit`

实现：`pulse/integrations/coding_plan/kimi.py`（`fetch_kimi_quota` / `parse_kimi_tiers`）。

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

**不在范围（M1–M3）**：Cursor MITM Credential Pool、Key Loan。

**M4 OpenAI 网关**（见 [ADR 0003](./adr/0003-openai-coding-plan-proxy.md)）：

| 能力 | 说明 |
|------|------|
| 客户端 | `base_url={Pulse}/openai/v1`，`Authorization: Bearer pkcp_…` |
| 签发 | `POST /api/v2/proxy-keys`，body 含 `coding_plan_vendor` |
| 入池 | `POST /api/v2/openai-proxy/accounts/{id}`，`cp_proxy_enabled` |
| 池列表 | `GET /api/v2/openai-proxy/pool?vendor=glm` |
| 转发 | `POST /openai/v1/chat/completions` → 厂家 OpenAI 兼容 upstream |

用量分析仍仅 Cursor 事件聚合；CP 网关用量后续可写 `proxy_key_usages`。

## 变更风险

上游未文档化字段变更时（参考 cc-switch issue #3036、#3652），应补充 fixture + 单测后再改解析；失败时账号状态 `unknown` / `sync_failed`，scheduler 跳过单账号不阻塞全局。
