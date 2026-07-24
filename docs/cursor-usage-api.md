# Cursor 用量 API 参考

本文档记录 Cursor 账号用量与 API Key 管理的非官方接口，用于脚本化查询（如 `cursor-usage.sh`）。  
这些接口来自 Cursor Agent CLI 与 Dashboard 的逆向分析，**非公开 API**，可能随时变更。

Dashboard 页面：[用量](https://cursor.com/dashboard/usage) · [API Keys](https://cursor.com/dashboard/api?section=user-keys#user-api-keys)

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

**注意：** `crsr_...` 只能用于 `exchange_user_api_key` 兑换 token；其余 `DashboardService` 接口（含用量与 Key 管理）均需 `accessToken`。`auth.json` 中缓存的 `accessToken` 过期后会返回 `401`，应重新 exchange。

**`accessToken` 有效期：** 实测约 **1 小时**（JWT `exp` 距兑换时刻约 3601 秒）。API Key 换出的 `refreshToken` **不能**通过 `POST /oauth/token` 续期（返回 `shouldLogout: true`）。

**脚本/服务缓存建议：** 短时间多次调用时，可在进程内按 `sha256(api_key)` 缓存 `accessToken`，用 JWT `exp` 判断失效，并在过期前 **5 分钟**提前刷新；收到 `401` 时清缓存并重新 exchange 一次。`crsr_...` 本身长期有效，可重复兑换。cursor-pulse 的 `CursorApiClient.get_access_token()` 已实现上述逻辑（仅内存、不落库）。

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

### GetHardLimit — 读取 On-Demand / 支出上限

对应 Dashboard [Spending](https://cursor.com/dashboard/spending) 的 **On-Demand Usage** 状态（「On-Demand Spending」开关与「Monthly Limit」）。

认证与其它 `DashboardService` 相同：先用 `crsr_...` 兑换 `accessToken`，再调用本接口。

**请求：** `{}`（团队场景可传 `teamId`）

**响应字段：**

| 字段 | 说明 |
|------|------|
| `hardLimit` | 月支出上限（美元整数）；未设置时可能为 `0` 或省略 |
| `noUsageBasedAllowed` | **关键字段**：`true` = On-Demand Spending **已关闭**（Dashboard 显示 Disabled）；`false`/缺省 = 允许按量扣费 |
| `hardLimitPerUser` | 团队人均上限（若有） |

**响应示例（已关闭）：**

```json
{
  "hardLimit": 0,
  "noUsageBasedAllowed": true
}
```

**响应示例（已开启，月限额 $50）：**

```json
{
  "hardLimit": 50,
  "noUsageBasedAllowed": false
}
```

**示例：**

```bash
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/GetHardLimit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{}'
```

---

### SetHardLimit — 设置 / 关闭 On-Demand Spending

写入 Dashboard Spending 页的 On-Demand 开关与月限额。  
**关闭 On-Demand**（与 UI 中 Monthly Limit 选 **Disabled** 等价）时传：

```json
{
  "hardLimit": 0,
  "noUsageBasedAllowed": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `hardLimit` | number | 是 | 月限额（美元整数）。关闭 On-Demand 时传 `0`；开启时可设为具体金额（如 `50`） |
| `noUsageBasedAllowed` | boolean | 是 | `true` = **关闭**按量扣费（禁用 On-Demand）；`false` = **开启** On-Demand |
| `teamId` | number | 否 | 团队场景 |
| `hardLimitPerUser` | number | 否 | 团队人均上限（若适用） |

**成功响应：** `{}`

**示例（强制关闭 On-Demand Spending）：**

```bash
# 1) API Key → accessToken
TOKEN="$(curl -sS -X POST "https://api2.cursor.sh/auth/exchange_user_api_key" \
  -H "Authorization: Bearer $CURSOR_API_KEY" \
  -H "Content-Type: application/json" -d '{}' | jq -r '.accessToken')"

# 2) 关闭 On-Demand（套餐用尽后不再超额扣费）
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/SetHardLimit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{"hardLimit":0,"noUsageBasedAllowed":true}'

# 3) 复核
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/GetHardLimit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{}'
# 期望：{"hardLimit":0,"noUsageBasedAllowed":true}
```

**开启 On-Demand 并设月限额 $100（一般不用于共享账号）：**

```bash
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/SetHardLimit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{"hardLimit":100,"noUsageBasedAllowed":false}'
```

**与 Dashboard UI 对照：**

| Dashboard Spending | API |
|--------------------|-----|
| On-Demand Spending = Disabled | `noUsageBasedAllowed: true` |
| On-Demand Spending 开启 + Monthly Limit 固定金额 | `noUsageBasedAllowed: false` + `hardLimit: <美元>` |
| Monthly Limit = Unlimited | `noUsageBasedAllowed: false`（`hardLimit` 行为以实测为准，勿对共享账号使用） |

cursor-pulse 在每次用量同步（`CursorSyncService.sync_account`）时会先 `GetHardLimit`，若未关闭则自动 `SetHardLimit(noUsageBasedAllowed=true)` 并钉钉通知管理员；关闭失败不阻断用量入库。实现见 `pulse/ingestion/on_demand.py`。

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

## User API Key 管理（api2.cursor.sh）

对应 Dashboard [API Keys → User API Keys](https://cursor.com/dashboard/api?section=user-keys#user-api-keys) 页面的「Add」与删除操作。  
**Cursor 官方文档未公开这些端点**；社区亦有 [程序化 Key 管理的功能请求](https://forum.cursor.com/t/programmatic-api-key-management-per-key-usage-cost-tracking/163762)。

认证与用量接口相同：先用 `exchange_user_api_key` 换取 `accessToken`，再调用下列方法。

**限制：**

- 必须先持有一个有效 `crsr_...` Key（或有效 session token）才能列出/创建/撤销 Key，无法「零 Key 起步」。
- 创建返回的完整 Key **只出现一次**，须立即保存；列表接口仅返回掩码 `crsr_...xxxx`。
- 撤销后 Key 立即失效，使用该 Key 的集成须同步更新。

---

### ListUserApiKeys — 列出当前账号的 User API Key

**请求：** `{}`

**响应示例：**

```json
{
  "apiKeys": [
    {
      "id": 345320,
      "maskedKey": "crsr_...e5f6",
      "name": "cli",
      "createdAt": "1783583347876"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `id` | Key 整数 ID；`RevokeUserApiKey` 使用此值 |
| `maskedKey` | 掩码后的 Key 前缀/后缀 |
| `name` | 创建时指定的名称 |
| `createdAt` | 创建时间（Unix 毫秒，字符串） |

**示例：**

```bash
TOKEN="$(curl -sS -X POST "https://api2.cursor.sh/auth/exchange_user_api_key" \
  -H "Authorization: Bearer $CURSOR_API_KEY" \
  -H "Content-Type: application/json" -d '{}' | jq -r '.accessToken')"

curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/ListUserApiKeys" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{}'
```

---

### CreateUserApiKey — 创建 User API Key

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | Key 显示名称，如 `"cli"`、`"cursor-pulse-sync"` |

**响应示例：**

```json
{
  "apiKey": "crsr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

**示例：**

```bash
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/CreateUserApiKey" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{"name":"my-script-key"}'
```

---

### RevokeUserApiKey — 撤销 / 删除 User API Key

对应 Dashboard 列表中的删除（垃圾桶）操作。

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | `ListUserApiKeys` 返回的整数 `id`（非 `crsr_` 字符串） |

**请求示例：**

```json
{ "id": 345320 }
```

**成功响应：** `{}`

**示例：**

```bash
curl -sS -X POST "https://api2.cursor.sh/aiserver.v1.DashboardService/RevokeUserApiKey" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Connect-Protocol-Version: 1" \
  -d '{"id":345320}'
```

---

### Team API Key 管理（团队管理员）

端点存在且路由可访问，认证方式与 User Key 相同。适用于 Dashboard 团队 Admin API Key 管理场景（需团队管理员权限，具体权限未在公开文档说明）。

| 方法 | 说明 |
|------|------|
| `ListTeamApiKeys` | 列出团队 API Key |
| `CreateTeamApiKey` | 创建团队 API Key |
| `RevokeTeamApiKey` | 撤销团队 API Key |

请求/响应结构与 User Key 对应方法类似；`RevokeTeamApiKey` 同样使用整数 `id`。

---

### Key 管理完整流程示例

```bash
API_KEY="crsr_..."   # 须为已存在的有效 Key

TOKEN="$(curl -sS -X POST "https://api2.cursor.sh/auth/exchange_user_api_key" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" -d '{}' | jq -r '.accessToken')"

# 列出
curl -sS -X POST ".../ListUserApiKeys" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Connect-Protocol-Version: 1" -d '{}'

# 创建（立即保存返回的 apiKey）
NEW_KEY="$(curl -sS -X POST ".../CreateUserApiKey" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Connect-Protocol-Version: 1" \
  -d '{"name":"rotation-backup"}' | jq -r '.apiKey')"

# 撤销（id 来自 ListUserApiKeys）
curl -sS -X POST ".../RevokeUserApiKey" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Connect-Protocol-Version: 1" \
  -d '{"id":345320}'
```

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

### POST /api/dashboard/get-hard-limit

网页侧读取 On-Demand / 支出上限；功能与 `GetHardLimit` 相同。  
**推荐脚本走 `api2` + Bearer**；仅在只有 Cookie、没有 User API Key 时使用本接口。

```bash
curl -sS -X POST 'https://cursor.com/api/dashboard/get-hard-limit' \
  -H 'Cookie: WorkosCursorSessionToken=YOUR_COOKIE' \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://cursor.com' \
  -d '{}'
```

### POST /api/dashboard/set-hard-limit

网页侧关闭 / 设置 On-Demand Spending；功能与 `SetHardLimit` 相同。  
POST 必须带 `Origin: https://cursor.com`，否则 CSRF 校验失败。

```bash
# 关闭 On-Demand Spending
curl -sS -X POST 'https://cursor.com/api/dashboard/set-hard-limit' \
  -H 'Cookie: WorkosCursorSessionToken=YOUR_COOKIE' \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://cursor.com' \
  -d '{"hardLimit":0,"noUsageBasedAllowed":true}'
```

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
| On-Demand / 支出上限读取 | `GetHardLimit` | 当前 |
| On-Demand 关闭 / 限额设置 | `SetHardLimit` | 当前 |
| User API Keys 列表 | `ListUserApiKeys` | — |
| User API Keys 创建 | `CreateUserApiKey` | — |
| User API Keys 删除 | `RevokeUserApiKey` | — |
| Team API Keys | `ListTeamApiKeys` / `CreateTeamApiKey` / `RevokeTeamApiKey` | 团队管理员 |
| Export CSV | 无服务端接口 | 自行分页导出 |

---

## 本地工具

仓库内脚本：`scripts/cursor-usage.sh`（可链接到 `~/.local/bin/cursor-usage`）

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
3. **Token 过期**：`accessToken` 约 1 小时有效；过期或临近过期（建议提前 5 分钟）时重新 `exchange_user_api_key`。进程内可缓存 token 以减少重复 exchange（见认证章节）。
4. **速率限制**：未公开；分页拉取时建议合理间隔。
5. **安全**：勿将 `crsr_...` API Key 或 `auth.json` 提交到版本库；泄露后可用 `RevokeUserApiKey` 或 Dashboard 撤销并重建。
6. **Key 管理非官方**：`ListUserApiKeys` / `CreateUserApiKey` / `RevokeUserApiKey` 未列入 [Cursor APIs Overview](https://cursor.com/docs/api)；生产环境自动化 Key 轮换请评估风险，优先保留人工 Dashboard 操作路径。

---

## 参考

- [Cursor CLI Slash Commands](https://cursor.com/docs/cli/reference/slash-commands)
- [Cursor APIs Overview](https://cursor.com/docs/api)
- [Unofficial dashboard API notes](https://gist.github.com/dmwyatt/1e9359b1862e7cbfe1e754fe4c8db764)（Cookie 方案）
- Agent CLI 逆向：`loginWithApiKey` → `POST /auth/exchange_user_api_key`；`CredentialManager.setAuthentication` → `auth.json`