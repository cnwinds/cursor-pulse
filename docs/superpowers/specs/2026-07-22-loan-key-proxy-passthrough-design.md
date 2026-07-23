# 借用 Key 走 Proxy 透传 + 用量综合展示

日期：2026-07-22  
状态：已确认（用户逐段批准）  
关联：

- `docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md`
- `docs/superpowers/specs/2026-07-22-proxy-key-reveal-command-design.md`
- `docs/superpowers/specs/2026-07-22-proxy-client-cli-agent-rename-design.md`

## 1. 目标

让「借用记录」里的 Cursor 借用 Key（真实 Cursor API Key，常见前缀 `cr` / `crsr_`）也能通过本项目 Go 代理工作：

1. 借用页提供与代理 Key 页一致的「复制命令」（Linux / PowerShell）。
2. Proxy 识别借用 Key：仅对**本系统已知的活跃借用**放行，用该 Key **透传**向 Cursor 换 JWT（不走池、不换号），同时继续 MITM 用量采集。
3. 借用页用量：**主数字仍为账号快照近似差值**；另展示「其中 proxy 精确费用」作为子集，二者不相加。

## 2. 已确认决策

| 点 | 结论 |
|---|---|
| 上游模式 | 透传：客户端借用 Key 直连 Cursor；不入代理池 |
| 放行范围 | 仅活跃 `KeyLoan` + 凭证 `status=active`（未知 `cr*` → 401） |
| 用量展示 | 主：账号近似差值；副：proxy 精确子集（不与主数字相加） |
| 复制命令 | 表格行 + 分配成功弹窗；模板与 `pk_` 一致，Key 换成借用明文 |
| 透传限额 | 不做 `pk_` 的 token/费用/5h 窗口限额 |
| CLI | `agent -k`；`HTTPS_PROXY` + `CURSOR_API_KEY` |
| 代理地址 | `.env` 的 `PROXY_PUBLIC_URL` |

## 3. 数据模型

### 3.1 `AiAccountCredential.key_hash`

- 新增可空列 `key_hash`：`sha256(plaintext)`，与 `pulse.proxy.keys.hash_proxy_key` 同算法。
- 签发借用 / 绑定 Cursor Key 时写入。
- 迁移：对能解密的活跃凭证回填；解不出的保持 `NULL`（此类 Key 无法走透传 authorize，直至重新绑定/签发）。

### 3.2 `ProxyKeyUsage` 归属扩展

| 字段 | 变更 |
|---|---|
| `proxy_key_id` | 改为可空（去掉对「必填」的依赖；历史行仍有值） |
| `loan_id` | 新增可空，索引；透传用量写入 |

应用层约束：每条用量 **恰好一个** 非空归属——`proxy_key_id`（`pk_` 路径）或 `loan_id`（借用透传）。`credential_id` 透传时必填为该借用凭证。

## 4. Authorize 契约

沿用 `POST /api/internal/v1/proxy/authorize`，请求体字段名仍为 `pulse_key`（值可为 `pk_...` 或借用 Cursor Key）。

| 输入 | 行为 |
|---|---|
| `pk_...` | 现有逻辑不变 |
| Cursor Key 前缀（实现约定：以 `cr` 开头，覆盖 `crsr_` 等） | `key_hash` 查活跃借用 + 活跃凭证 |
| 其他 | `invalid` |

透传成功响应：

```json
{
  "status": "ok",
  "mode": "loan_passthrough",
  "proxy_key_id": null,
  "loan_id": "<uuid>",
  "credential_id": "<uuid>",
  "reason": null
}
```

- 借用已撤销 / 凭证非 active → `status: invalid`（撤销即失效）。
- `mode=loan_passthrough` 时不做 window/quota 评估。

## 5. Go 数据面

### 5.1 SessionBinding 扩展

增加 `Mode`、`LoanID`、`CredentialID`；`PulseKey` 在透传路径存客户端借用 Key（供续 JWT）。

### 5.2 Exchange

1. 调 Pulse authorize。
2. `mode=loan_passthrough`：不取池；用客户端 Key 向 Cursor 真 exchange；绑定会话后返回 `accessToken`。
3. `pk_` / unlimited / quota：逻辑不变。

### 5.3 业务 MITM

| | 池模式 | `loan_passthrough` |
|---|---|---|
| 上游 Authorization | 池内 JWT（可换号） | 仅该借用 Key 换出的 JWT |
| 耗尽 / 401 | 轮换池 | 不轮换；事件带 `loan_id`；失败返回 |
| 会话 | JWT → `proxy_key_id` | JWT → `loan_id` + `credential_id` |

透传维护按 `credential_id` 缓存的单 Key entry（复用现有 exchange/续期逻辑），不写入共享池。

### 5.4 用量上报

- Tap 逻辑不变；payload 增加可选 `loan_id`。
- 透传：`loan_id` + `credential_id`，`proxy_key_id` 空。
- `pk_`：仅 `proxy_key_id`（现状）。
- 两条归属都缺：丢弃该条并打日志。

前缀可在 Go 做快路径提示，**权威仍在 Pulse**（未知 `cr*` → 401）。

## 6. 管理 API 与前端

### 6.1 API

| 接口 | 说明 |
|---|---|
| `GET /api/v2/loans/{id}/client-setup?shell=bash\|powershell` | 解密借用凭证 → `{ plaintext_key, proxy_url, shell, command }` |
| `loan_payload` | 增补 `proxy_cost_cents`（可选 `proxy_total_tokens`）= `sum(ProxyKeyUsage where loan_id=…)` |

- 鉴权：与借用管理一致（`accounts:write`）。
- 仅 active 且可解密 → 200；已撤销 / 不可还原 → 410；不存在 → 404。
- 命令复用 `build_client_command`；`proxy_url` ← `PROXY_PUBLIC_URL`。
- 分配成功已返回 `api_key` 时，前端可本地拼命令，少一次 setup。

### 6.2 命令模板

PowerShell：

```powershell
$env:HTTPS_PROXY = "<PROXY_PUBLIC_URL>"
$env:CURSOR_API_KEY = "<loan_cursor_key>"
agent -k
```

bash：

```bash
export HTTPS_PROXY="<PROXY_PUBLIC_URL>"
export CURSOR_API_KEY="<loan_cursor_key>"
agent -k
```

### 6.3 前端（`LoansView`）

- 进行中行：「复制命令」→ Linux / PowerShell → client-setup → 剪贴板。
- 分配成功弹窗：保留「复制 Key」；增加「复制命令」；说明可用代理命令走 proxy。
- 「近似消耗」：主行 `$X.XX`；副行始终显示 `其中 proxy $Y.YY`（含 `$0.00`）。
- 页头说明：消耗为账号用量差值近似；走 proxy 的部分另有精确子计数。

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 未知 / 已撤销的 Cursor Key | authorize `invalid`；exchange 401 |
| Pulse authorize 不可用 | fail-closed（503），与现网一致 |
| 透传 Cursor exchange 失败 | 归类返回；不轮换池 |
| 透传业务 401 | 事件带 `loan_id`；需重新 exchange |
| client-setup 解密失败 | 410 |
| usage 无归属 ID | 丢弃并记日志 |

## 8. 测试要点

- Pulse：活跃借用 `cr*` → `loan_passthrough`；撤销后 `invalid`；`pk_` 回归。
- Pulse：仅 `loan_id` 的 usage 可写入；列表 `proxy_cost_cents` 正确。
- Go：透传不碰池；业务只用该 Key；池模式回归。
- API/前端：client-setup 含 `HTTPS_PROXY`、借用 Key、`agent -k`；撤销后 410。

不做完整浏览器 E2E。

## 9. 非目标

- 钉钉「我的借用」下发代理命令（可后续）。
- 强制禁止直连，或把近似与 proxy 费用相加。
- 透传路径的 `pk_` 式额度限额。
- 修改历史 plan 文档中的旧表述。
- 方案 2（Go 本地借用白名单热更新）。

## 10. 实现边界（模块）

| 模块 | 改动摘要 |
|---|---|
| `pulse/storage` | `key_hash`、`ProxyKeyUsage.loan_id` / `proxy_key_id` 可空 + migrate |
| `pulse/proxy` + `internal_proxy_api` | authorize 借用分支；usage 写 `loan_id` |
| `pulse/tool_center` + `quota_api` | 签发写 hash；client-setup；payload 汇总 |
| `proxy/`（Go） | binding、exchange/MITM 透传、usage payload |
| `web-admin` `LoansView.vue` | 复制命令 + 用量副行 |
