# Cursor 代理服务集成设计（Go 数据面 + Pulse 控制面）

日期：2026-07-22
状态：已确认（用户逐段批准）

## 1. 背景与目标

将 Cursor 代理能力集成进 cursor-pulse，作为服务器端集中代理：每个用户把 cursor-agent 指向服务器，由服务器自动完成上游代理与 cursor key 分配。

- 用户持有本项目签发的**脉冲 key**（格式 `pk_<urlsafe>`），作为代理访问凭证。
- 按脉冲 key 类型提供两种 cursor key 分配策略：
  - **畅享模式**（`unlimited`）：从所有可用 cursor 账号中选取 key 使用（限额自动换号）。
  - **限额模式**（`quota`）：配置 token 总量额度 / 费用额度 / 可选 5 小时窗口限额，超额停用。

### 已确认的决策

| 决策点 | 结论 |
|---|---|
| 客户端 | cursor-agent CLI，以 `--insecure`（`-k`）跳过证书校验，免装 CA |
| 代理形态 | MITM 正向代理（CONNECT，`HTTPS_PROXY=http://服务器:端口`），借鉴 comate-cursor-proxy（Go 版） |
| key 池来源 | 复用现有台账 `AiAccount` / `AiAccountCredential`，标记"可用于代理"后入池 |
| 额度语义 | token 总量额度 + 费用额度（现有定价体系折算）+ 可选 5h 窗口限额；不配独占 key，畅享/限额共享同一池 |
| 架构 | 方案 B：Go 数据面（comate-cursor-proxy 改造）+ Pulse 控制面（Python/FastAPI 扩展） |

### 安全说明

`--insecure` 使用户到服务器一段不校验证书，同网段攻击者理论上可劫持该段。内网/可信网络可接受；公网部署后续提供 CA 可选安装。agent↔服务器之间仍是 TLS 加密（自签证书），服务器↔api2.cursor.sh 为正常校验的 TLS。

## 2. 总体架构

```
cursor-agent --insecure
   │ HTTPS_PROXY=http://服务器:8317  (CONNECT MITM，仅 *.cursor.sh)
   ▼
Go 代理（数据面，独立进程，由 comate-cursor-proxy 改造）
   ├─ 拦截 /auth/exchange_user_api_key → 带脉冲 key 向 Pulse 授权
   ├─ 按授权从池选 cursor key → 换真 JWT 返回给 agent（记 JWT→脉冲key 映射）
   ├─ 业务请求：换号/流式重放（现有 Go 逻辑）
   └─ 旁路解析 token 用量 → 批量上报 Pulse
   ▲ 内部 API（共享 token 鉴权，同现有 /api/internal/v1/* 模式）
   │
Pulse（控制面，现有进程扩展 pulse/proxy/ 模块）
   ├─ 脉冲 key CRUD：模式、token 额度、费用额度、5h 窗口额度
   ├─ 用量记账 + 三级额度评估 + 超额停用
   ├─ 台账 credential 标记"可代理"（池来源）
   └─ web-admin：脉冲 key 管理、池状态、用量看板
```

## 3. 数据模型（Pulse 库，`pulse/storage/models.py`）

### `ProxyKey`（脉冲 key）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `key_hash` | str unique | sha256(脉冲 key)，不明文存储 |
| `key_hint` | str | key 前 8 位，展示用 |
| `name` | str | 备注名 |
| `member_id` | FK → Member | 归属门户用户 |
| `mode` | str | `unlimited`（畅享）/ `quota`（限额） |
| `token_limit` | int nullable | 总 token 额度（空=不限） |
| `cost_limit_cents` | int nullable | 费用额度（空=不限） |
| `window_5h_token_limit` | int nullable | 5 小时滑动窗口 token 限额（空=不限） |
| `status` | str | `active` / `suspended` / `revoked` |
| `suspended_reason` | str nullable | |
| `expires_at` / `created_at` / `updated_at` | datetime | |

### `ProxyKeyUsage`（用量账本，每次请求一条）

`id`、`proxy_key_id`(FK)、`credential_id`(FK → AiAccountCredential，实际使用的 cursor key)、`model`、5 类 token（input/output/cache_read/cache_write/reasoning）、`cost_cents`、`ts`。

- 总 token 用量 = 全表 sum；5h 窗口用量 = `ts >= now-5h` 的 sum。

### `ProxyEvent`（事件日志）

rotation / exhausted / suspended / resumed 事件，供看板展示。

### cursor key 池

不新增表：`AiAccountCredential` 增加 `proxy_enabled` 布尔字段。运行时冷却/耗尽状态由 Go 代理内存维护，事件上报 Pulse。

## 4. 内部 API（`/api/internal/v1/proxy/*`）

鉴权：复用现有 internal 共享 token 模式（`PULSE_INTERNAL_SERVICE_TOKEN`）。

### `POST /api/internal/v1/proxy/authorize`

Go 代理拦截 agent 的 key 兑换请求时调用。

- 请求：`{pulse_key}`
- 响应：`{status: ok | invalid | suspended | window_limited, proxy_key_id?, mode, reason?}`
  （`ok`/`window_limited` 时返回 `proxy_key_id`，供 usage 上报归属）
- Go 侧缓存结果（TTL 60s）。

### `GET /api/internal/v1/proxy/pool`

Go 代理每 60s 拉取：所有 `proxy_enabled=true` 且启用状态的 cursor 凭证（服务端 AES-GCM 解密后下发，仅内网 IPC）。每项含 `{credential_id, api_key}`。Go 内存池热更新。

### `POST /api/internal/v1/proxy/usage`

每请求完成后批量异步上报：

```json
[{"proxy_key_id": 1, "credential_id": 1, "model": "...",
  "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0},
  "ts": "..."}]
```

Pulse 落账并评估三级额度。

### `POST /api/internal/v1/proxy/events`

换号/耗尽/停用事件，best-effort。

## 5. Go 代理改造点（数据面）

基线：comate-cursor-proxy（Go，零依赖），代码引入本仓库（如 `proxy/` 目录）。

### 新增 `pulse_client.go`

- `authorize(pulseKey)` / `fetchPool()` / `reportUsage(batch)` / `reportEvent()`
- 授权缓存 60s；usage 批量缓冲（满 50 条或 5s flush）、失败重试（有上限，超限丢弃并记日志）
- **Pulse 不可达策略**：新 authorize 请求 fail-closed；已缓存授权 TTL 内继续放行

### 修改 `pool.go`

- key 来源从静态 `-keys` 改为 `GET /pool` 定期拉取，热更新（保留同 key 冷却状态）
- JWT 兑换/缓存、`exhausted` 标记、轮转游标逻辑原样保留

### 修改 `mitm.go`（核心改动）

原 `/auth/*` 透传（`skipAuth`）改为本地拦截 exchange：

1. agent 启动 → `POST /auth/exchange_user_api_key`，`Authorization: Bearer <脉冲key>`
   - `invalid` / `suspended` → 401/403 + reason
   - `window_limited` → Connect rate-limit 错误（agent 提示稍后重试）
   - `ok` → 池当前可用账号换真 JWT，内存记录 `JWT → 脉冲key` 映射，按上游响应格式返回
2. 业务请求 `Bearer <池JWT>` → 查映射得归属 → 正常转发（换号/流式重放逻辑不变）
3. 映射丢失（代理重启）→ 401 → agent 自动重新 exchange → 重新授权

### 新增 `usage_tap.go`

- 移植 Node 版 `proto.js` 的 `TurnEndedUpdate`（field 14）5 类 token 旁路提取
- 复用 `connect.go` 已有的极简 protobuf wire-format 遍历基础
- 解析失败不影响转发（best-effort）

## 6. 请求数据流

```
agent --insecure ──CONNECT──> Go 代理
  1. exchange: Bearer pk_xxx → authorize → 池JWT 返回（记 JWT→pk 映射）
  2. 业务请求: Bearer 池JWT → 查映射 → 转发 api2.cursor.sh
     ├─ 限额错误（流末/非200）→ 换号重放（agent 无感）
     └─ 流中旁路解析 token 用量
  3. 请求结束 → usage 批量上报 Pulse → 超额则 suspend
  4. 该 pk 的 authorize 缓存刷新后拿到 suspended → 拒绝
```

**超额生效时滞**：usage 异步上报 + authorize 60s 缓存 + 已签发池 JWT 有效期（≤30min），suspend 后用户已持有的池 JWT 仍可能短期可用。额度管理场景可接受；不做每请求同步授权（代价过大）。备选（不采用）：代理截短 JWT 有效期至 5 分钟换取近实时。

## 7. 限额记账与停用规则（Pulse 侧 `pulse/proxy/` 模块）

- **记账时机**：`POST /usage` 落账后同步评估该脉冲 key
- **三级语义**：
  - `token_limit` / `cost_limit_cents` 超额 → `status=suspended`（硬停用），管理员调额或手动恢复后复活
  - `window_5h_token_limit` 超额 → 不 suspend，authorize 返回 `window_limited`，窗口滑过自动恢复
  - `unlimited` 模式 → 照常记账（看板用），永不评估
- **费用折算**：复用现有定价体系（model→单价），Pulse 侧计算 `cost_cents`，Go 代理不涉及价格
- **恢复通道**：后台"恢复"按钮；调高额度后 authorize 自动重新评估通过

## 8. 管理后台（web-admin）

1. `ProxyKeysView.vue` — 脉冲 key 列表（hint/归属/模式/用量进度/状态）、创建（选模式+三类额度，完整 key 仅创建时显示一次）、吊销/恢复/编辑额度
2. 代理池页（并入 Accounts 或独立 `ProxyPoolView.vue`）— credential "可代理"开关、冷却/耗尽事件展示
3. 用量看板（ProxyKeysView 详情抽屉）— token/费用趋势、5h 窗口用量、cursor 账号分布
4. 权限码：`proxy:read` / `proxy:write`（owner/operator 角色）

## 9. 错误处理

| 场景 | 行为 |
|---|---|
| Pulse 不可达 | 新 authorize fail-closed；缓存内放行；usage 缓冲重试（超限丢弃+日志） |
| cursor 池全耗尽 | 503 `all API keys exhausted`（事件上报） |
| agent 未填脉冲 key | exchange 无 Authorization → 401 |
| 脉冲 key suspended | authorize 403 + reason |
| JWT→pk 映射丢失 | 401 → agent 自动重新 exchange |
| 上游传输错误 | 不标记坏 key，原样重试（现有逻辑） |

## 10. 测试

- **Go**（假上游测试模式）：exchange 拦截与授权映射、suspended 拒绝、usage 旁路解析（含流中途限额）、池热更新、批量上报与重试
- **Python**（pytest）：authorize 各状态分支、三级额度评估（含 5h 窗口滑动）、suspend/恢复流转、定价折算、pool 接口只下发 `proxy_enabled` 凭证
- **集成**：端到端脚本——Pulse + Go 代理 + 假上游，跑"授权→请求→上报→超额→停用→恢复"全流程

## 11. 实施顺序

1. Pulse 侧：模型+迁移 → 内部 API → 记账评估 → 管理 API + 页面
2. Go 侧：引入基线代码 → `pulse_client` → exchange 拦截 → usage 旁路 → 池热更新
3. 端到端联调 + 部署脚本（`cursor-pulse.bat/.sh` 挂代理进程）

## 12. 风险与边界

- 多账号高频轮换可能触发 Cursor 风控（`SUSPICIOUS_USAGE_BLOCKED`），沿用基线项目的自知风险
- cursor 凭证经内部 API 解密下发，仅限内网/本机 IPC，走共享 token 鉴权
- 本代理仅支持 cursor-agent CLI（`--insecure` + `HTTPS_PROXY`），不支持 Cursor IDE 桌面端
- 用量旁路解析为 best-effort，Cursor 协议变更可能导致 token 统计缺失（不影响代理转发）
