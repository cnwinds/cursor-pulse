# ADR-0005：IDE 共享端口 + 作用域 IDE Key（pkide_）

- 状态：已实现（2026-10-03）；专属端口过渡期已结束（v0.7.0 之后删除）
- 日期：2026-10-03
- 相关：`pulse/proxy/ide_keys.py`、`pulse/proxy/authorize.py`、`pulse/web/proxy_keys_api.py`、`pulse/web/quota_api.py`、`proxy/server.go`、`proxy/mitm.go`、`proxy/ide_setup.go`、`web-admin/src/components/CopyCommandDialog.vue`、`CONTEXT.md`

## 背景

早期 IDE 接入为每把 Proxy Key 分配**专属监听端口**（`GET /ide-port`，默认自 9100 起，映射持久化在 `ide_ports.json`）。成员把 Cursor `settings.json` 的 `http.proxy` 指到该端口，代理在 CONNECT 层把端口反查为 key，从而在 IDE 侧完成归因。

该方案的问题：

1. **运维成本高**：每接入一名成员多开一个端口，防火墙需放行整段端口；映射与 key 明文落在代理状态目录。
2. **凭据面过大**：按端口方案会在 `/ide-port?key=` URL 中暴露父 key 全文，并明文写入 `ide_ports.json`；任何共享端口方案又必须把凭据写进 `settings.json` 的 `http.proxy`，而 Cursor Settings Sync 会把它复制到其他设备——因此必须是收窄作用域的 key，绝不能是 `pk_` / `pka_`。
3. **与 Cursor 多栈并存**：IDE 同时有扩展宿主（Node / Electron）与 Chromium 子进程；专属端口方案假设「计费流量一定走带 key 的隧道」，但 Cursor 实现可能把部分请求挪到无凭据栈，导致静默丢计量或需要 `-ide-pulse-key` 这类共享兜底 key，进一步模糊归因边界。

## 技术验证（已核实）

在受控抓包与代码阅读中确认：

1. **扩展宿主**（Cursor 扩展进程，经 `@vscode/proxy-agent`）在 `http.proxy` 带 userinfo 时**保留**用户名密码，CONNECT 时发送 `Proxy-Authorization: Basic`，用户名即 Pulse 下发的 key。
2. **Chromium 子进程**按 Chromium 规则**剥离** URL userinfo，CONNECT **不带**代理认证。
3. 实测计费族请求（含 `AgentService/RunSSE`、`BidiAppend` 等）在扩展宿主隧道上携带 Basic 认证；Chromium 隧道仅承载 `/auth/full_stripe_profile`、`/extensions-control`、遥测、Sentry 等非计费路径。

因此：共享主端口 + `http.proxy` userinfo 中的 `pkide_` 足以让扩展宿主归因，且不必再为每 key 开端口。

**重要**：「计费流量全部来自扩展宿主」是**当前 Cursor 实现的观察结果**，不是对外契约。若未来版本把计费端点迁入 Chromium 栈，无凭据隧道上的计费请求将收到 **401**，代理同时打出限频日志 `[ide] billing request on unauthenticated tunnel path=...` 作为漂移探针。

## 决策

### 作用域 IDE Key（pkide_）

- 前缀 `pkide_`，由父 **quota** `ProxyKey` 或 **`delivery_mode=proxy_alias`** 的 `KeyLoan` 派生；**不**单独占 `proxy_keys` 一行。
- 存储：父行上 `ide_key_hash`（唯一索引）、`ide_key_hint`、`ide_encrypted_key`；每个父实体**同时仅一把**有效 `pkide_`。
- 签发：`get_or_issue_ide_key` 有密文则解密返回，否则生成；`rotate_ide_key` 覆盖旧值并**立即**失效旧 key。
- `cursor_direct` 借用继续交付借用人已有的 `cr*`（成员本已持有完整 key，收窄无意义）；`pkcp_` 不支持 IDE key。
- **授权等价于父 key**：`authorize(pkide_)` 调用与 `pk_` / `pka_` 相同的行级判定（`_authorize_proxy_key_row` / `_authorize_loan_alias_row`），返回字段逐字相同，仅多 `"scope": "ide"`。用量、模型路由、限额、选号与父 key 一致，记到父 `proxy_key_id` 或 `loan_id`（后续成员级 spend-check 仍按同一父 ID 校验）。
- **禁止扩大凭据面**：`pkide_` 不能 exchange、不能给 CLI、不能走 OpenAI 网关（`pkcp_` 路径不变）。

### 共享主端口 + 隧道内校验

- 新客户端：`http.proxy = "http://<pkide_>:x@<host>:<主端口>"`（key URL 编码；密码固定 `x`）。
- `handleConnect` 解析 `Proxy-Authorization: Basic` 的用户名为隧道 key，**CONNECT 从不返回 407、从不拒绝**；key 与来源（`userinfo`）写入请求上下文。
- 计费闸门（`bindIDESession`）在隧道内业务请求层校验：
  - 隧道 key 为空 → **401** `IDE proxy key missing: re-run setup-cursor.ps1`，并限频打 `[ide] billing request on unauthenticated tunnel` 日志。
  - `userinfo` 来源且前缀不是 `pkide_` 或 `cr` → **401** `full proxy key not allowed in proxy URL; use IDE key`。
  - `pkide_` 调 exchange → **403** `IDE key not allowed for exchange`（前缀 + `scope=ide` 双保险）。
- CLI 交换会话仅在「隧道 key 非空、且不是本代理签发的 opaque token、且与绑定 key 不同」时才换绑；避免 HTTPS_PROXY 带 userinfo 时冲掉 CLI 会话。

### 删除 `-ide-pulse-key`

- 移除 `-ide-pulse-key` / `PROXY_IDE_PULSE_KEY` / 配置 `ide_pulse_key` 的运行时回退。
- 若仍配置，启动打 **ERROR** 说明已移除并忽略（不 fatal，避免拖垮 CLI）。
- 主端口上**不再**有共享全权 key 兜底；IDE 必须重新跑带 `pkide_` 的 setup 命令。与 userinfo 里的 `pk_`/`pka_` 401 并存时，不得再依赖 `-ide-pulse-key` 作为过渡，否则会重新引入全权泄露面。

### Settings Sync

setup 脚本把 `http.proxy` 追加进 `settingsSync.ignoredSettings`（去重），避免 IDE key 随 Cursor Settings Sync 同步到其他设备。uninstall 反向移除该项。

### 专属端口（已删除）

- v0.7.0 保留 `idePortRegistry`、`GET/DELETE /ide-port`、`PROXY_IDE_PORT_BASE` 与专属监听作为一版过渡；此后已删除端口子系统、`ide_ports.json` 持久化及 uninstall 中的遗留 `DELETE /ide-port` 段。`/ide-port` 现返回 404。
- 仍配置 `PROXY_IDE_PORT_BASE` / `-ide-port-base` 时，与 `-ide-pulse-key` 同样处理：启动打 ERROR 并忽略。
- 卸载：`irm .../uninstall-cursor.ps1 | iex`。

### API

- `GET .../client-setup?kind=ide`：quota key 与 `proxy_alias` 借用返回 `pkide_` 命令；`cursor_direct` 仍返回 `cr*`。
- `POST .../ide-key/rotate`：proxy key 与 loan 各一；loan 侧借用人或 `accounts:write` 管理员；`cursor_direct` → 400，非 active → 410；响应形状同 `kind=ide` client-setup（loan 含 `delivery_mode`）。

### Pulse 对外入口

除内部 `POST /api/internal/v1/proxy/authorize` 外，Pulse 管理 API **不接受** `pkide_` 作为调用凭据（无「用 IDE key 调 Pulse」的入口）。

## 测试（摘要）

**Python**（`tests/test_ide_keys.py` 等）：

- `pkide_` 与父 key 在各种状态（正常、吊销、暂停、过期、超限、`pka_` manual/auto/pool）下授权等价（除 `scope=ide`）。
- `kind=ide` client-setup 返回 `pkide_`；重复请求同父实体拿到同一把；rotate 后旧 key `unknown_key`；`cursor_direct` 不变。
- loan rotate 权限与 400/410 边界。

**Go**（`proxy/ide_test.go` 等）：

- userinfo `pkide_` 归因与 RunSSE 用量记账；无凭据隧道计费 401 + 漂移日志；userinfo `pk_` 401；`pkide_` exchange 403；CLI 会话不被 userinfo 换绑；Chromium 式无凭据非计费透传。
- `-ide-pulse-key` 配置后不生效；setup 脚本拒绝 `pk_`、写入 userinfo 主端口 URL、`ignoredSettings`；`/ide-port` 返回 404。

## 后果

- 成员需在控制台复制**新** IDE 命令（含 `pkide_`）并重跑 setup；专属端口已删除，未迁移的客户端 `http.proxy` 指向的端口不再监听，IDE 将无法连接代理。
- 防火墙只需放行主端口，不再规划 per-key 端口段。
- `pkide_` 泄露仍只影响 IDE 归因通道，不能换票给 CLI；但仍应通过 rotate 与父 key 吊销级联失效。
- Cursor 若改变计费栈分布，会先以 401 + 日志暴露，需产品侧跟进而非静默丢账。
