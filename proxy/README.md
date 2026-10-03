# cursor-pulse-proxy

Cursor CLI（agent）多账号额度透明轮换代理，作为 **Pulse 数据面** 运行：从控制面拉取凭证池、拦截 exchange 做会话授权、上报用量与换号事件。

> 与系统 `HTTP_PROXY`、控制面互调不是同一层。分层与变量对照见 [docs/PROXY_LAYERS.md](../docs/PROXY_LAYERS.md)。

## 构建

需要 Go 1.22+（零第三方依赖）：

```powershell
go build -o cursor-pulse-proxy.exe .
```

## 启动（Pulse 模式，推荐）

设置控制面地址与内部服务 token 后启动：

```powershell
$env:PULSE_BASE_URL = "http://127.0.0.1:8080"
$env:PULSE_INTERNAL_SERVICE_TOKEN = "pulse-internal-dev"
.\cursor-pulse-proxy.exe -listen 127.0.0.1:8317
```

- 默认监听 `0.0.0.0:8317`（Docker 友好）；本机开发建议 `127.0.0.1:8317` 或 `PROXY_LISTEN=127.0.0.1:8317`。
- CONNECT 目标默认允许 `*.cursor.sh`、`*.cursorapi.com`（扩展市场）与 `*.cursor.com`（后两类 blind tunnel，不 MITM）；`PROXY_CONNECT_ALLOWLIST` 可覆盖，非匹配主机返回 403。
- 也可通过 `-pulse-url` / `-pulse-token` 或配置文件 `pulse_url` / `pulse_token` 传入。
- CA 证书：`%USERPROFILE%\.cursor-quota-proxy\ca.pem`（首次运行自动生成）。
- 启动后代理会周期性从 Pulse 拉取凭证池；日志中应出现 `[pool] hot-updated: N credential(s)`。

## 客户端

### Cursor CLI（agent）

让 agent 走 HTTPS 代理，并使用 Pulse 签发的 proxy key（`pk_...`）：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:8317"
$env:CURSOR_API_KEY = "pk_..."
agent -k
```

不想配 CA 时，用 `-k` / `--insecure` 即可；也可设 `$env:NODE_EXTRA_CA_CERTS` 指向 `ca.pem`。

### Cursor IDE（一键接入）

IDE 与 CLI 走同一代理与凭证池，但认证形态不同：CLI 用 API key 调 `exchange_user_api_key` 换会话 JWT，IDE 用 WorkOS 登录 JWT 直接请求业务端点。代理以 **Pulse 模式** 启动即可同时服务两种客户端（无需 IDE 专用配置）：

```powershell
$env:PULSE_BASE_URL = "http://127.0.0.1:8080"
$env:PULSE_INTERNAL_SERVICE_TOKEN = "pulse-internal-dev"
.\cursor-pulse-proxy.exe -listen 0.0.0.0:8317
```

**成员侧一条命令接入**（Windows；`-Key` 为 web-admin「复制命令 → Cursor IDE」下发的 **IDE 专用密钥** `pkide_`，或 `cursor_direct` 借用的 `cr*`；**不是** CLI 用的 `pk_` / `pka_`）：

```powershell
& ([scriptblock]::Create((irm "http://<代理地址>:8317/setup-cursor.ps1"))) -Key "pkide_..."
```

脚本：安装 CA 到当前用户受信任根 → 把 `http.proxy` 写成带 userinfo 的**主端口** URL（`http://<pkide_>:x@host:port`）→ 写入 `cursor.general.disableHttp2`、`http.systemCertificates` → 把 `http.proxy` 加入 `settingsSync.ignoredSettings`（避免 Settings Sync 把 key 同步到其他设备）→ Cursor 未运行则自动拉起。传入 `pk_` / `pka_` 会直接报错，提示从控制台复制 IDE 命令。之后登录 Cursor 即用；`pkide_` 经隧道 userinfo 归因，限额/吊销与父 key 一致。

CA 也可单独取：`http://<代理地址>:8317/ca.pem`。

**卸载（同样一条命令）**：

```powershell
irm http://<代理地址>:8317/uninstall-cursor.ps1 | iex
```

脚本幂等，且在代理不可达时仍完成本地清理：移除 settings.json 的 `http.proxy` / `cursor.general.disableHttp2` / `http.systemCertificates` 三项，并移除 `settingsSync.ignoredSettings` 中的 `http.proxy`（安装前备份 `settings.json.bak-cursor-pulse` 保留不动；若接入前你本来配有自己的 `http.proxy`，安装时已被覆盖，卸载只删除不恢复，请从备份手工找回）、删除当前用户受信任根中 Subject 含 `cursor-quota-proxy` 的全部证书（按 Subject 匹配、逐个按指纹删除——同名的其他代理 CA 也会一并移除）。卸载后完全退出并重启 Cursor 即恢复直连。

管理台「共享池代理 / 借用」的复制命令下拉中已有 **「Cursor IDE」** 项（`GET /api/v2/proxy-keys/{id}/client-setup?kind=ide` 或 `GET /api/v2/loans/{id}/client-setup?kind=ide`），生成的就是上面这条带 key 的一键接入命令。`proxy_alias` 借用可在借用记录中 **重置 IDE 密钥**（`POST .../ide-key/rotate`），旧 `pkide_` 立即失效，各端须重跑新命令。可选开启 `PROXY_IDE_LOCK_SUB=1` 把每把代理密钥锁定到首次使用的登录身份，防 key 外借（解析 JWT `sub`，拒绝 `alg=none`；**不校验签名**；锁在代理内存中，重启后重新认领）。

**401 / 403 排障（IDE）**：

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| `IDE proxy key missing: re-run setup-cursor.ps1` | 计费请求走在无凭据隧道上（常见于未跑 setup、key 丢失，或 Cursor 把计费挪到 Chromium 栈） | 从控制台复制 IDE 命令并重跑 setup；查代理日志 `[ide] billing request on unauthenticated tunnel` |
| `full proxy key not allowed in proxy URL; use IDE key` | `http.proxy` userinfo 里是 `pk_` / `pka_` | 复制 **Cursor IDE** 命令（`pkide_`），勿把 CLI 密钥写入 IDE |
| `IDE key not allowed for exchange` | 用 `pkide_` 调了 CLI exchange | CLI（agent / `CURSOR_API_KEY`）请使用 `pk_`/`pka_`；`pkide_` 仅用于 IDE `http.proxy` |
| 403 `ide_sub_mismatch` | 开启了 `PROXY_IDE_LOCK_SUB` 且登录身份与首次不同 | 用原账号登录，或管理员轮换 key / 重启代理后重新认领 |

IDE 接入的行为与限制：

- **授权三分**：① **计费**——只有已知计费路径（`/agent.v1.*`、`/aiserver.v1.*` 且非身份 RPC）走会话校验、池选号、失败标记与用量入账；② **会话换回**——其余路径上若 bearer 是本代理 exchange 签发的不透明 token（HMAC 可验，上游必拒），换成该会话的池凭证转发，但不计量、不标记池；③ **直通**——其余一切（IDE 登录 JWT 访问身份族 RPC `GetMe`、`GetUserProfile`、`GetTeams`、`GetTeamCommands`、`GetUserStatus`，含 TOFU 绑定之后；`/auth/*`；遥测；未知服务族）原样转发客户端自己的 token，不选号、不计量、不标记。改写 IDE 身份 RPC 会让身份一致性校验失败（GetMe 无限重试）；未知路径 fail-open 为不归因：新端点最多丢计量，IDE 与 CLI 都不会断功能。
- IDE 界面账号显示成员自己的登录账号；模型列表、用量等业务数据来自实际服务的池账号。
- IDE 聊天走 `agent.v1.AgentService/RunSSE`：响应 Content-Type 标为 `text/event-stream` 但实体是标准 Connect 信封帧，代理按 Connect 流中继（逐帧 flush + TurnEnded usage tap），用量与 CLI 同管线入账。
- **流式聊天（`RunSSE`）经上游翻墙代理可能 stall**（实测 clash 会挂起长流），IDE 场景优先直连，仅被墙域走 `PROXY_UPSTREAM_URL`。
- 对话历史按服务账号在服务端存储：sticky 驻留期内连续，轮换后可能切换会话归属（Switch dwell 缓解）。
- 共享主端口 + `http.proxy` userinfo 归因已生效。登录身份锁（`PROXY_IDE_LOCK_SUB`）在主端口生效，开启时要求可解析的 JWT `sub`（无 sub 拒绝绑定）；锁状态不持久化——代理重启后首个登录重新认领。
- 吊销 / 挂起生效：TOFU 绑定走 `AuthorizeFresh`（即时）；已绑定会话与 CLI 一样，最多延迟到 `PROXY_SESSION_TTL`（默认 120s）后的重授权。`pkide_` 会出现在本机 `settings.json` 的 `http.proxy` userinfo 中——按「知晓 key 即可接入」信任边界运维。
- 主端口上的 CLI exchange 会话不会被 IDE `http.proxy` userinfo 冲掉；勿在 IDE 与 CLI 之间混用 `pkide_` 与 `pk_`/`pka_`。

调试开关（默认关闭）：`PROXY_DEBUG_HTTP=1`（请求/响应行）、`PROXY_DEBUG_HEADERS=1`（checksum/client-key 等头）、`PROXY_DEBUG_STREAM=1`（帧转储，含 RunSSE 请求体，用于重放分析）。

**每 key 专属端口已删除**：v0.7.0 之后不再提供 `/ide-port`（返回 404）与专属监听；仍指向旧专属端口的客户端须在控制台复制新 IDE 命令重跑 setup。防火墙只需放行主端口。

**平台范围**：安装/卸载一键命令面向 **Windows**（PowerShell 5.1）。macOS/Linux 的 Cursor IDE 按手工步骤接入：① 把 `http://<代理>:8317/ca.pem` 装入系统信任 store（macOS `security add-trusted-cert`；Linux 复制到 ca-certificates 后 `update-ca-certificates`）；② 编辑 `~/.config/Cursor/User/settings.json` 写入 `http.proxy`（带 userinfo 的主端口 URL，如 `http://<pkide_>:x@host:8317`）、`cursor.general.disableHttp2: true`、`http.systemCertificates: true`，并可选把 `http.proxy` 加入 `settingsSync.ignoredSettings`；③ 重启 Cursor。卸载即反向移除这三项（及 `settingsSync.ignoredSettings` 中的 `http.proxy`）并删除 CA。


### 出站上游代理（翻墙）

Go 进程访问 Cursor 时可经单独配置的上游代理（**不要**用 `HTTPS_PROXY`，以免自环）：

```powershell
# .env 或进程环境变量
$env:PROXY_UPSTREAM_URL = "http://127.0.0.1:7890"
# 或带认证：
# $env:PROXY_UPSTREAM_URL = "http://user:pass@127.0.0.1:7890"
```

也可用 `-upstream-proxy` 覆盖。Pulse 控制面仍直连。

## 与控制面联调检查表

1. **Admin**：在凭证上开启 `proxy_enabled`。
2. **池非空**：代理日志出现 `[pool] hot-updated: N credential(s)`（N > 0）。
3. **成员用 Key**：在 web-admin「借用记录」用自动分配签发 `pka_…`，它与账号池共用轮换（Cursor IDE 的 `-Key` 用同一把）。历史 `pk_…` 在「账号池 → 历史接入密钥」。指定借用的 `pka_…` 绑定单一账号，**不能**当作整池 `pk_` 使用。
4. **Authorize 冒烟**：`POST /api/internal/v1/proxy/authorize`（Bearer `PULSE_INTERNAL_SERVICE_TOKEN`）对 `pk_...` 返回 200。
5. **Agent 跑一条**：agent 经代理完成一次对话。
6. **用量可见**：web-admin 用量抽屉出现对应记录。

## 开发环境挂载（cursor-pulse）

```powershell
.\cursor-pulse.bat start              # web/admin/channel/assistant
.\cursor-pulse.bat start proxy        # Go 数据面 :8317（首次自动 go build）
.\cursor-pulse.bat status
.\cursor-pulse.bat log proxy -f
.\cursor-pulse.bat stop proxy
```

`proxy` **不在**默认 `start` 集合（避免无 Go 环境失败）。DevManager 会把项目根目录 `.env` 注入子进程，因此需配置 `PULSE_BASE_URL` 与 `PULSE_INTERNAL_SERVICE_TOKEN`。

## 部署说明

开发机优先用上一节 `cursor-pulse start proxy`。生产/独立部署可：

- 手动跑二进制（见「启动（Pulse 模式）」），或
- Docker（与主栈分离）：

```bash
cd docker
# 主栈已 up；.env 含 PULSE_INTERNAL_SERVICE_TOKEN
docker compose -f docker-compose.proxy.yml up -d --build
```

默认经 `host.docker.internal` 访问宿主机 Web；CA 落在 `docker/proxy-data/`。`docker/.env` 挂载为 `/app/.env`，改令牌 / `PROXY_UPSTREAM_URL` 等后 `docker compose -f docker-compose.proxy.yml restart` 即可生效（改端口需 `up -d` 重建）。详见 [docs/RUNBOOK.md](../docs/RUNBOOK.md)。

## 本地 `-keys` 兜底（无 Pulse）

离线或纯本地开发时，可沿用旧版本地 key 池模式（无会话门控、无用量上报）：

```powershell
.\cursor-pulse-proxy.exe -keys "key1,key2,key3"
```

Key 会写入 `%USERPROFILE%\.cursor-quota-proxy\config.json`，之后启动无需再传 `-keys`。

## 原理（简述）

- agent 的 API key 用于向 `api2.cursor.sh/auth/exchange_user_api_key` 换取 JWT；Pulse 模式下该 exchange 由代理拦截，用 `pk_...`（共享池）或 `pka_...`（借贷 alias）授权并映射到池内 Cursor 凭证。会话绑定默认 **120s** 后向 Pulse 重新 authorize（`-session-ttl` / `PROXY_SESSION_TTL`）。
- **会员用量规则与余额**（ADR-0006）在 `AgentService/Run` 前经 Pulse `spend-check` 校验，超限以 `429 resource_exhausted` 拒绝，不在 exchange 登录阶段失败（避免 CLI 误报 “API key is invalid”）；校验不可用时 fail-closed 返回 503。撤销 / 停用 / 未知 key 仍在 exchange 失败。
- 配额/限流错误时自动换凭证重放，CLI 侧无感（流式路径在尚未转发数据时可整体重放）。
- 通过 `HTTPS_PROXY` + 自签 CA（MITM `*.cursor.sh`）实现，无需修改 agent 本体。

## 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `-listen` | 监听地址 | `0.0.0.0:8317`（本机可用 `127.0.0.1:8317`；环境变量 `PROXY_LISTEN`） |
| `-pulse-url` | Pulse 控制面 base URL | 环境变量 `PULSE_BASE_URL` |
| `-pulse-token` | Pulse 内部服务 token | 环境变量 `PULSE_INTERNAL_SERVICE_TOKEN` |
| `-upstream-proxy` | Cursor 出站上游代理 | 环境变量 `PROXY_UPSTREAM_URL` |
| `-session-ttl` | 会话重授权间隔 | 环境变量 `PROXY_SESSION_TTL`（默认 120s） |
| `-sticky-min-dwell` | sticky 最小驻留（Switch dwell） | 环境变量 `PROXY_STICKY_MIN_DWELL`（默认 20m；`0`/`off` 关闭） |
| `-ide-pulse-key` | **已移除**（若仍设置则启动 ERROR 并忽略） | 曾用环境变量 `PROXY_IDE_PULSE_KEY`、配置 `ide_pulse_key`；请改用控制台 IDE 命令中的 `pkide_` |
| `-ide-port-base` | **已移除**（若仍设置则启动 ERROR 并忽略） | 曾用环境变量 `PROXY_IDE_PORT_BASE`；请改用主端口 + `pkide_` userinfo |
| — | IDE 登录身份锁：每把代理密钥锁定首次出现的登录 JWT `sub`（不同身份 403） | 环境变量 `PROXY_IDE_LOCK_SUB=1`（默认关闭） |
| `-keys` | 逗号分隔 Cursor API key（本地兜底） | 读配置文件 |
| `-dir` | 状态目录（CA、配置） | `~/.cursor-quota-proxy` |
| `-config` | 配置文件路径 | `<dir>/config.json` |

环境变量 `PROXY_CONNECT_ALLOWLIST`（默认 `*.cursor.sh,cursor.sh`）限制 CONNECT 可连主机；非匹配目标返回 403。

## 资源限制

| 限制 | 默认 | 环境变量 |
|---|---|---|
| 非流式请求体 | 32 MiB | `PROXY_MAX_BODY`（字节数；MITM 与 `/openai/v1/chat/completions` 共用） |
| 流式 usage tap 缓冲 | 8 MiB | —（超限后停止解析，仍转发） |
| 每连接读头超时 | 30s | —（根 `http.Server` 与 MITM 连接均设置） |
| 每连接空闲超时 | 120s | — |
| 池 exhausted 周期清零 | 30m | `PROXY_EXHAUSTED_RESET`（`0`/`off`/`false` 关闭） |
| sticky 最小驻留 | 20m | `PROXY_STICKY_MIN_DWELL`（`0`/`off`/`false` 关闭） |

`/openai/v1/chat/completions` 在 `stream: true` 时会自动注入 `stream_options.include_usage`，边转发 SSE 边解析末包 `usage` 上报 Pulse（与 Cursor MITM 流式 tap 共用 8 MiB 行缓冲上限）。

## Switch dwell（sticky 最小驻留）

会话的每个 sticky 槽（`AutoSticky` / `APISticky`，见下文「两个 sticky 槽」）在**两次请求间隔小于驻留阈值**期间不因「本桶耗尽」而轮转（默认 20 分钟）——只记日志并继续用当前账号。认证失败（`badUntil` 冷却）与全池耗尽仍立即轮转，避免会话卡在不可用账号上。

- 计时基准是该槽 **距上次服务的空闲间隔**（`LastActive`；无该字段时回退 `Since`）。选号/续用时刷新，请求转发**结束**时再刷新一次：一个跑了 25 分钟的 agent 长流，驻留从流结束时算起；流还在跑时，该槽一律视为在驻留期内，并发进来的同桶请求不会把它换走。密集聊天会持续刷新 `LastActive`，不会因「绑定总时长」到点而换号。
- `Since` 只在该槽**真正切换**账号时重置；同一账号续用不刷新。
- 槽没有活动时间戳（零值）→ 不驻留。
- 驻留只保护已经填上的槽；另一个桶的槽是空的时候照常按本桶表选号，不看对方的驻留。
- Web 侧对应语义见 `loan_selection.min_switch_minutes`（评分侧降权），两层独立生效。

## 借用候选白名单（自动分配借用）

`loan_alias` 绑定可以带 `AllowedCredentialIDs`——Pulse 在 authorize 响应里下发的候选 primary 凭证白名单（`credential_ids`）。带白名单时借用走**与共享池相同的选择逻辑**：per-session sticky + Switch dwell + 按 Quota Pool，只在白名单内轮转，不会逃到借用人无权使用的账号。

- 白名单为空 → 回退 `passthroughToken`：固定在发放时那把 `key_role=loan` 的 Cursor Key（**指定借用**）。
- 白名单每次 authorize 都会**整体替换**（不是合并），这样被移出候选的账号下一次请求就不再服务。
- 借用人自己名下的账号在 Pulse 侧就被排除；当前绑定账号始终保留在白名单首位，避免借用人瞬间失去正在用的账号。
- 借用流量的用量与轮转事件按**实际服务账号**（`entry.credentialID`）归因，而不是发放时绑定的那把 Key。

## 同时在线人数

换票、会话续期、sticky 槽为空或因为额度耗尽要换号时，代理调用 `POST /api/internal/v1/proxy/authorize` 选座：`quota_pool`（`auto` / `api`，缺省 `auto`）说明是哪个槽，`current_credential_id` 是这个槽当前的凭证（离开时再带 `release_current: true`），`held_credential_ids` 是同一会话另一个**活跃**槽的凭证（续座；即使与 current 相同，释放 current 也不拆它的座位）。槽活跃 = 有请求正在转发，或 3 分钟内服务过（与 Pulse 默认座位 TTL 一致）。闲置的槽不再上报，座位随 TTL 过期；它下次再用时先带 `current_credential_id`（不释放）重新选座：账号未满就留在原账号，已满则按本桶表换一个，都没有座位则拒绝。会话续期优先报 auto 槽，只有 auto 槽闲置而 api 槽活跃时才报 api 槽。Web 按人计座：同一成员在同一个账号上只占一席，同时用两个账号则各占一席。默认同一账号不超过 3 个经代理的同时使用者（`max_concurrent_users`，0 为不限制）。心跳超过 `concurrent_ttl_seconds`（默认 180s，长于会话 TTL）视为离开。

- 响应里 `seat_advised=true` 且 `assigned_credential_id` 有值：用这个凭证，不要在每次心跳时改选全局第一名。
- `seat_advised=true` 且分配为空：没有可去的未满账号（或正在离开的凭证不能再选回去）。换号或新加入时不要再塞进满员账号。
- `seat_advised=false`：Web 没有按人数做判断（没有候选，或顾问失败）。按本地池选号；池本身是空的，就是密钥耗尽，不是人数上限。
- 顾问请求失败：沿用本地选号，并跳过上次返回的 `blocked_credential_ids`。
- Web 只按 `quota_pool` 那张打分表的顺序分配，且只分配该桶仍有快照余量的账号，与本地 `availableFor` 判断一致。借用白名单同样先按该桶打分表排序再过滤。
- 指定借用仍固定在原 Key 上，只是占一个座位。
- 参数在 web-admin「系统设置 → 选号规则」。

## 两个 sticky 槽

只有 auto 和 api 两个 Quota Pool。`Run` 请求按模型归桶：Auto / Composer / BYOK 第三方模型，以及**没带模型的请求**（登录、非 `Run` 路径、5s 内没等到模型）一律算 auto，其余算 api。

- 每个 **CLI session JWT** 同时记两个 sticky 槽：`AutoSticky`、`APISticky`。exchange 时只按 auto 表填 `AutoSticky`；`APISticky` 在第一次 api 请求时按 api 表填。
- auto 请求只用 `AutoSticky`，api 请求只用 `APISticky`，各自按自己那张顺序表选号、各自换号、各自驻留。即使 auto 槽的账号 api 桶还有余量，api 请求也不会顺用它——必须按 api 表选。
- 每个槽记进行中的请求数（转发开始加 1、结束减 1，含出错与客户端断开）。agent 的 `Run` 流可能一跑几十分钟而没有新请求，靠这个计数识别该槽仍在使用。
- 两个槽可以落在同一个账号上（该账号恰好两张表都排前面），此时只占一个座位。
- 额度耗尽后的 `RotateOnExhaustion` 只动失败请求所属的那个槽，而且只在该槽确实是这个凭证时才动。

## 池 exhausted 语义

- 配额按 Cursor **Auto+Composer** 与 **API** 两桶分别标记（`autoQuotaExhausted` / `apiQuotaExhausted`）；仅当两桶都耗尽时凭证才视为 fully unavailable。
- Pulse `/pool` 下发 `credentials_by_pool.auto` / `.api` 两张顺序表（`credentials` 只是两表并集，用于按 ID 查找）以及 `auto_pct` / `api_pct` 快照。
- 某凭证因 **配额/限流** 被标记后，运行时耗尽标志 **sticky**：Pulse 热更新凭证池时 **保留**（避免短暂恢复后立刻再烧额度）。
- **auth/exchange 失败** 走 `badUntil` 短冷却（约 2 分钟），热更新会清掉。
- 本地回退（没有 Pulse 选座建议）时也按本桶顺序表**从头**找第一个可用账号，不从当前位置往后绕。
- 热更新 **保留** `cur` 指针，不会每 60s 把池子打回 index 0。
- 进程默认每 **30m** 清一次 **runtime 额度耗尽标志**（不清 `badUntil`），并将 `cur` 置 0（`PROXY_EXHAUSTED_RESET`；设为 `0`/`off`/`false` 可关闭）。auth 冷却仍靠 TTL / 热更新。
- 日志：`[pool] runtime quota marks reset (N keys)`（全量 `reset()` 仍为 `[pool] exhaustion flags reset`）。

## 开发

```powershell
go test ./...
```

测试覆盖：Pulse 客户端、池热更新、exchange 拦截与会话映射、Connect/ErrorDetails 解析、流式 usage tap 与换号事件等。
