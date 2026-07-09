# Cursor 用量 API 参考

本文档记录 Cursor 账号用量数据的非官方接口，用于脚本化查询（如 `tools/cursor-usage.sh`）。  
这些接口来自 Cursor Agent CLI 与 Dashboard 的逆向分析，**非公开 API**，可能随时变更。

Dashboard 页面：[https://cursor.com/dashboard/usage](https://cursor.com/dashboard/usage)

---

## 概览

Cursor 用量数据有两套接入方式：

| 来源 | 基础 URL | 认证 | 适用场景 |
|------|----------|------|----------|
| **Agent / CLI 接口** | `https://api2.cursor.sh` | `Authorization: Bearer <accessToken>` | 个人 Pro/Pro+/Ultra；可用 API Key 换 token |
| **网页 Dashboard 接口** | `https://cursor.com/api/*` | `WorkosCursorSessionToken` Cookie | 浏览器登录态；Bearer token **无效** |
| **团队 Admin API**（官方） | `https://api.cursor.com` | Basic Auth（`crsr_...` Admin Key） | Enterprise / Team 管理员 |

个人账号推荐走 **`api2.cursor.sh`**，与 `agent login` / `CURSOR_API_KEY` 共用同一套认证。

---

## 认证

### 1. API Key 兑换 Session Token（推荐用于脚本）

User API Key 格式：`crsr_...`（在 [Dashboard → API Keys](https://cursor.com/dashboard) 创建）。

```http
POST https://api2.cursor.sh/auth/exchange_user_api_key
Content-Type: application/json
Authorization: Bearer crsr_...

{}
```

**成功响应：**

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ..."
}
```

**失败示例：**

```json
{ "code": "error", "message": "Invalid User API Key" }
```

Agent CLI 启动时若设置 `CURSOR_API_KEY`，会调用此接口并将结果写入 `auth.json`（见下文）。

### 2. 本地 auth.json

| 平台 | 路径 |
|------|------|
| Linux | `~/.config/cursor/auth.json` |
| macOS | `~/.cursor/auth.json`（或 Keychain） |
| Windows | `%APPDATA%\Cursor\auth.json` |

由 `agent login` 或 API Key 兑换生成，典型内容：

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "apiKey": "crsr_..."
}
```

文件权限：`0600`；目录：`0700`。

### 3. 后续请求

所有 `api2.cursor.sh` 的 Dashboard 接口均使用：

```http
Authorization: Bearer <accessToken>
Content-Type: application/json
Connect-Protocol-Version: 1
```

Connect RPC 风格：路径为 `POST /aiserver.v1.DashboardService/<MethodName>`，body 为 JSON。

### 4. 网页 Cookie 认证（仅 cursor.com）

从浏览器 DevTools → Application → Cookies → `cursor.com` 复制 `WorkosCursorSessionToken`。  
POST 到 `cursor.com` 时还需：

```http
Origin: https://cursor.com
```

---

## Agent 接口（api2.cursor.sh）

基础路径：

```
POST https://api2.cursor.sh/aiserver.v1.DashboardService/<Method>
```

通用请求头：

```http
Authorization: Bearer <accessToken>
Content-Type: application/json
Connect-Protocol-Version: 1
```

时间参数均为 **Unix 毫秒时间戳（字符串）**。JSON 字段同时支持 **camelCase** 与 **snake_case**（如 `startDate` / `start_date`）。

---

### GetCurrentPeriodUsage — 当前计费周期汇总

对应 Dashboard 顶部的「已用 / 剩余 / 百分比」。

**请求：**

```json
{}
```

**响应字段：**

| 字段 | 说明 |
|------|------|
| `billingCycleStart` / `billingCycleEnd` | 计费周期起止（毫秒） |
| `planUsage.totalSpend` | 已消耗金额（美分） |
| `planUsage.includedSpend` | 计入套餐的部分（美分） |
| `planUsage.remaining` | 剩余额度（美分） |
| `planUsage.limit` | 套餐包含额度（美分） |
| `planUsage.autoPercentUsed` | Auto 模型占用占比 |
| `planUsage.apiPercentUsed` | 指定/API 模型占用占比 |
| `planUsage.totalPercentUsed` | 总占用占比 |
| `displayMessage` | 展示文案，如 "You've used 53% of your included usage" |
| `autoModelSelectedDisplayMessage` | Auto 模型分项文案 |
| `namedModelSelectedDisplayMessage` | 指定模型分项文案 |
| `spendLimitUsage` | On-demand 支出上限（`limitType`: `user` / `team`） |
| `autoBucketModels` | 计入 Auto 桶的模型列表 |

**示例：**

```bash
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{}'
```

---

### GetPlanInfo — 套餐信息

**请求：** `{}`

**响应示例：**

```json
{
  "planInfo": {
    "planName": "Pro+",
    "includedAmountCents": 7000,
    "price": "$60/mo",
    "billingCycleEnd": "1784958141000",
    "planOwner": "PLAN_OWNER_STRIPE"
  },
  "nextUpgrade": {
    "tier": "ultra",
    "name": "Ultra",
    "includedAmountCents": 40000,
    "price": "$200/mo",
    "description": "..."
  }
}
```

---

### GetFilteredUsageEvents — 逐条用量明细

对应 Dashboard 用量表格；支持分页与时间过滤。

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `startDate` / `start_date` | string | 否 | 起始时间（毫秒） |
| `endDate` / `end_date` | string | 否 | 结束时间（毫秒） |
| `page` | number | 否 | 页码，从 1 开始，默认 1 |
| `pageSize` / `page_size` | number | 否 | 每页条数，默认 100 |
| `userId` / `user_id` | number | 否 | 按用户过滤（团队管理员） |
| `teamId` / `team_id` | number | 否 | 按团队过滤 |
| `modelId` / `model_id` | string | 否 | 按模型过滤 |

**响应：**

```json
{
  "totalUsageEventsCount": 175,
  "usageEventsDisplay": [
    {
      "timestamp": "1783591555915",
      "model": "composer-2.5",
      "kind": "USAGE_EVENT_KIND_INCLUDED_IN_PRO_PLUS",
      "requestsCosts": 0.9,
      "usageBasedCosts": "-",
      "isTokenBasedCall": true,
      "tokenUsage": {
        "inputTokens": 3595,
        "outputTokens": 998,
        "cacheReadTokens": 151103,
        "cacheWriteTokens": 0,
        "totalCents": 3.45131
      },
      "owningUser": "376012765",
      "owningTeam": "2168997",
      "cursorTokenFee": 0,
      "isChargeable": true,
      "isHeadless": false,
      "chargedCents": 3.45131,
      "conversationId": "8ff011fb-7f01-4e74-bbe1-e026d47ea50f"
    }
  ]
}
```

**事件字段说明：**

| 字段 | 说明 |
|------|------|
| `timestamp` | 事件时间（毫秒字符串） |
| `model` | 使用的模型 |
| `kind` | 计费类型，如 `USAGE_EVENT_KIND_INCLUDED_IN_PRO_PLUS`、`USAGE_EVENT_KIND_USAGE_BASED` |
| `tokenUsage` | 输入/输出/cache token 与 `totalCents` |
| `chargedCents` | 实际计费（美分），通常含 Cursor Token Fee |
| `requestsCosts` | 按请求数计费时的消耗单位 |
| `usageBasedCosts` | 格式化的美元字符串；套餐内可能为 `"-"` |
| `conversationId` | 关联的对话 ID |
| `isHeadless` | 是否来自 Headless / 后台 Agent |

**分页拉取全部：**

```bash
PAGE=1
while true; do
  RESP=$(curl -sS -X POST ".../GetFilteredUsageEvents" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Connect-Protocol-Version: 1" \
    -d "{\"page\":$PAGE,\"pageSize\":100,\"startDate\":\"$START\",\"endDate\":\"$END\"}")
  echo "$RESP" | jq '.usageEventsDisplay[]'
  TOTAL=$(echo "$RESP" | jq '.totalUsageEventsCount')
  COUNT=$(echo "$RESP" | jq '.usageEventsDisplay | length')
  [ "$COUNT" -eq 0 ] && break
  PAGE=$((PAGE + 1))
done
```

Dashboard 的「Export CSV」为前端导出，**无独立 CSV 接口**；需自行分页后写 CSV。

---

### GetAggregatedUsageEvents — 按模型汇总

对应 Dashboard 图表/按模型统计。

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `startDate` / `start_date` | string | 否 | 起始时间（毫秒） |
| `endDate` / `end_date` | string | 否 | 结束时间（毫秒） |
| `userId` / `user_id` | number | 否 | 按用户过滤 |
| `teamId` / `team_id` | number | 否 | 按团队过滤 |

**时间范围行为：**

- **不传日期**：默认为**当前计费周期**（`totalCents` 之和与 `GetCurrentPeriodUsage.planUsage.totalSpend` 一致）。
- **传入 `startDate` / `endDate`**：仅统计该区间内的用量。
- 无数据的区间返回 `{}` 或空 `aggregations`。

**响应示例：**

```json
{
  "aggregations": [
    {
      "modelIntent": "composer-2.5",
      "inputTokens": "4861279",
      "outputTokens": "726233",
      "cacheReadTokens": "87013884",
      "cacheWriteTokens": "0",
      "totalCents": 2167.68593,
      "tier": 2
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `modelIntent` | 模型标识 |
| `inputTokens` / `outputTokens` | 输入/输出 token（字符串数字） |
| `cacheReadTokens` / `cacheWriteTokens` | Cache 读写 token |
| `totalCents` | 该模型在区间内的总费用（美分） |
| `tier` | 计费档位 |

---

### GetCreditGrantsBalance — 赠送额度

**请求：** `{}`

部分账号返回空对象 `{}`；有赠送额度时返回余额相关字段。

---

### GET /auth/usage — 旧版请求数统计

```http
GET https://api2.cursor.sh/auth/usage
Authorization: Bearer <accessToken>
```

**响应示例（Pro+ 账号通常无有效限额）：**

```json
{
  "gpt-4": {
    "numRequests": 0,
    "numRequestsTotal": 0,
    "numTokens": 0,
    "maxTokenUsage": null,
    "maxRequestUsage": null
  },
  "startOfMonth": "2026-06-25T05:42:21.000Z"
}
```

Enterprise 等按请求数计费的账号更有参考价值；Pro/Pro+ 以 `GetCurrentPeriodUsage` 为准。

---

## 网页接口（cursor.com，Cookie 认证）

以下接口需要浏览器 Cookie `WorkosCursorSessionToken`，**不能**用 Agent 的 `accessToken` 直接访问。

### GET /api/usage-summary

```bash
curl -sS 'https://cursor.com/api/usage-summary' \
  -H 'Cookie: WorkosCursorSessionToken=YOUR_COOKIE'
```

返回计费周期、`membershipType`、`individualUsage`（plan / onDemand）、`teamUsage` 等。  
功能与 `GetCurrentPeriodUsage` 重叠，但字段结构不同（含 on-demand 细分）。

### POST /api/dashboard/get-filtered-usage-events

```bash
curl -sS -X POST 'https://cursor.com/api/dashboard/get-filtered-usage-events' \
  -H 'Cookie: WorkosCursorSessionToken=YOUR_COOKIE' \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://cursor.com' \
  -d '{"page":1,"pageSize":100}'
```

参数与 `GetFilteredUsageEvents` 类似。  
**推荐**：个人脚本优先用 `api2.cursor.sh` 的 `GetFilteredUsageEvents`，无需 Cookie。

### GET /api/usage?user=<workos_user_id>

旧版简单计数，基本已被上述接口取代。

---

## 团队 Admin API（官方）

文档：[Cursor API Overview](https://cursor.com/docs/api) · [Admin API](https://cursor.com/docs/account/teams/admin-api)

- 基础 URL：`https://api.cursor.com`
- 认证：`curl -u YOUR_ADMIN_API_KEY:`
- 需 Enterprise / Team 管理员权限

常用端点：

| 端点 | 说明 |
|------|------|
| `POST /teams/spend` | 当前计费周期团队支出 |
| `POST /teams/filtered-usage-events` | 团队用量明细 |
| `POST /teams/daily-usage-data` | 按日汇总（建议最多每小时轮询一次） |
| `GET /teams/members` | 团队成员列表 |

个人 User API Key（`crsr_...`）**不能**直接调用 Admin API；Admin Key 也**不能**兑换为 `GetCurrentPeriodUsage` 所需的 session token。

---

## 接口与 Dashboard 对照

| Dashboard 内容 | api2 接口 | 时间范围 |
|----------------|-----------|----------|
| 周期用量汇总 | `GetCurrentPeriodUsage` | 当前计费周期 |
| 套餐名称 / 价格 | `GetPlanInfo` | 当前 |
| 用量明细表 | `GetFilteredUsageEvents` | 可指定；支持分页 |
| 按模型图表 | `GetAggregatedUsageEvents` | 可指定；默认当前周期 |
| 赠送额度 | `GetCreditGrantsBalance` | — |
| Export CSV | 无服务端接口 | 自行分页导出 |

---

## 本地工具

仓库内脚本：`tools/cursor-usage.sh`（已链接到 `~/.local/bin/cursor-usage`）

```bash
# API Key
cursor-usage --api-key crsr_...

# 环境变量
CURSOR_API_KEY=crsr_... cursor-usage

# 本地 auth.json
cursor-usage --auth-file ~/.config/cursor/auth.json

# 原始 JSON（GetCurrentPeriodUsage）
cursor-usage --json
```

当前脚本仅输出周期汇总；明细与按模型汇总可按本文档自行调用对应接口，或后续扩展脚本。

---

## 注意事项

1. **非官方 API**：未在 Cursor 公开文档中保证稳定性，升级 Agent 后可能变化。
2. **金额单位**：`planUsage`、事件中的 `*Cents` 字段均为 **美元美分**（7000 = $70.00）。
3. **Token 过期**：`accessToken` 为 JWT，过期后可用 `auth.json` 中保存的 `apiKey` 重新调用 `exchange_user_api_key`，与 Agent CLI 行为一致。
4. **速率限制**：未公开；分页拉取时建议合理间隔。
5. **安全**：勿将 `crsr_...` API Key 或 `auth.json` 提交到版本库；泄露后应在 Dashboard 撤销并重建。

---

## 参考

- [Cursor CLI Slash Commands](https://cursor.com/docs/cli/reference/slash-commands)
- [Cursor APIs Overview](https://cursor.com/docs/api)
- [Unofficial dashboard API notes](https://gist.github.com/dmwyatt/1e9359b1862e7cbfe1e754fe4c8db764)（Cookie 方案）
- Agent CLI 逆向：`loginWithApiKey` → `POST /auth/exchange_user_api_key`；`CredentialManager.setAuthentication` → `auth.json`