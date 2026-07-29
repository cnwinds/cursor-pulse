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
- CONNECT 目标默认仅允许 `*.cursor.sh` 与 `cursor.sh`（`PROXY_CONNECT_ALLOWLIST`）；其它主机返回 403。
- 也可通过 `-pulse-url` / `-pulse-token` 或配置文件 `pulse_url` / `pulse_token` 传入。
- CA 证书：`%USERPROFILE%\.cursor-quota-proxy\ca.pem`（首次运行自动生成）。
- 启动后代理会周期性从 Pulse 拉取凭证池；日志中应出现 `[pool] hot-updated: N credential(s)`。

## 客户端

让 agent 走 HTTPS 代理，并使用 Pulse 签发的 proxy key（`pk_...`）：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:8317"
$env:CURSOR_API_KEY = "pk_..."
agent -k
```

不想配 CA 时，用 `-k` / `--insecure` 即可；也可设 `$env:NODE_EXTRA_CA_CERTS` 指向 `ca.pem`。

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
3. **创建 Pulse key**：在 web-admin 创建共享池 proxy key（`pk_...`）。借贷 alias 为 `pka_...`，走独立 authorize 路径，**不能**当作 `pk_` 使用。
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
| `-keys` | 逗号分隔 Cursor API key（本地兜底） | 读配置文件 |
| `-dir` | 状态目录（CA、配置） | `~/.cursor-quota-proxy` |
| `-config` | 配置文件路径 | `<dir>/config.json` |

环境变量 `PROXY_CONNECT_ALLOWLIST`（默认 `*.cursor.sh,cursor.sh`）限制 CONNECT 可连主机；非匹配目标返回 403。

## 资源限制

| 限制 | 默认 | 环境变量 |
|---|---|---|
| 非流式请求体 | 32 MiB | `PROXY_MAX_BODY`（字节数） |
| 流式 usage tap 缓冲 | 8 MiB | —（超限后停止解析，仍转发） |
| 每连接读头超时 | 30s | — |
| 每连接空闲超时 | 120s | — |

## 池 exhausted 语义

- 某凭证因 **配额/限流** 被 `markExhausted` 后，`exhausted` 标志 **sticky**：Pulse 热更新凭证池时 **保留**（避免短暂恢复后立刻再烧额度）。
- **auth/exchange 失败** 走 `badUntil` 短冷却（约 2 分钟），热更新会清掉。
- Pulse 模式下每个 **CLI session JWT** 在 exchange 时绑定一个池内凭证（`StickyCredentialID`），同一会话内所有 `Run`（含 subagent）复用该号，直至额度用尽后按池顺序切到下一个可用号。
- 热更新 **保留** `cur` 指针，不会每 60s 把池子打回 index 0。
- 进程默认 **不** 周期性 reset exhausted（`PROXY_EXHAUSTED_RESET` 未设置或为 `0`/`off`）。需要时可设为如 `30m`，会清掉所有 `exhausted` 与 `badUntil` 并将 `cur` 置 0。
- 日志：`[pool] exhaustion flags reset`（仅当启用了 reset 时）。

## 开发

```powershell
go test ./...
```

测试覆盖：Pulse 客户端、池热更新、exchange 拦截与会话映射、Connect/ErrorDetails 解析、流式 usage tap 与换号事件等。
