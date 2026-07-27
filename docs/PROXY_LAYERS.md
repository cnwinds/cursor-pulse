# 代理分层说明

同叫「代理」的配置实际是三套机制，不要混用变量。

## 总览

| 层 | 用途 | 关键变量 | 消费者 |
|---|---|---|---|
| **A. Cursor MITM** | 替换 Key、计量、会话授权 | `PROXY_PUBLIC_URL`、`PROXY_LISTEN`、`PULSE_INTERNAL_SERVICE_TOKEN`、`PROXY_PULSE_BASE_URL` | 用户 CLI；Go 代理 `:8317` |
| **B. 进程内互调** | Web↔Assistant、Assistant→Pulse | `ASSISTANT_MIRROR_BASE_URL`、`ASSISTANT_SERVICE_TOKEN`、`PULSE_BASE_URL` | web / assistant / channel |
| **C. 出站翻墙** | 访问外网 Cursor / LLM / 钉钉 | 宿主机 `HTTP(S)_PROXY`；Go 侧 **`PROXY_UPSTREAM_URL`** | Python 出站；Go 上游 Cursor |

```
用户 Cursor CLI
  │  HTTPS_PROXY = PROXY_PUBLIC_URL (:8317)     ← 层 A
  ▼
Go MITM (cursor-pulse-proxy)
  ├─ authorize / usage ──直连──▶ Pulse web       ← 层 B（控制面）
  └─ Cursor API ──PROXY_UPSTREAM_URL──▶ 翻墙代理 ← 层 C

Pulse web / channel
  ├─ Assistant mirror ──内部直连──▶ :8090         ← 层 B（trust_env=False）
  └─ Cursor / 钉钉 / LLM ──可走 HTTP_PROXY──▶     ← 层 C（trust_env=True）
```

## 规则

1. **Internal never proxies**：控制面互调使用 [`pulse/http_clients.py`](../pulse/http_clients.py) 的 `internal_client`（`trust_env=False`）。
2. **Outbound may proxy**：公网调用用 `outbound_client`（`trust_env=True`），可走系统 `HTTP(S)_PROXY`。
3. **MITM ≠ 系统代理**：用户侧 `HTTPS_PROXY` 只指向 `:8317`；Go 翻墙只用 `PROXY_UPSTREAM_URL`（勿对 Go 进程设 `HTTPS_PROXY=:8317`，会自环）。
4. 本地 `cursor-pulse.sh` 子进程会注入 `NO_PROXY=127.0.0.1,localhost,::1` 作双保险。

## 变量速查

| 变量 | 层 | 含义 |
|------|----|------|
| `PROXY_PUBLIC_URL` | A | 写入一键命令，用户设为 `HTTPS_PROXY` |
| `PROXY_LISTEN` | A | Go 监听地址（Docker 常用 `0.0.0.0:8317`） |
| `PROXY_PULSE_BASE_URL` | A→B | Docker 独立 proxy 栈连宿主机 Web（勿用主栈 `http://web:8080`） |
| `PULSE_BASE_URL` | B | Assistant /（本机）Go 访问 Pulse |
| `ASSISTANT_MIRROR_BASE_URL` | B | Pulse → Assistant |
| `PROXY_UPSTREAM_URL` | C | Go → Cursor 的翻墙代理 |
| `HTTP_PROXY` / `HTTPS_PROXY`（宿主机） | C | Python 出站（Cursor sync、钉钉 OAuth 等） |

## 常见故障

| 现象 | 原因 | 处理 |
|------|------|------|
| 会话账本 / Assistant BFF **502**，`/health` 正常 | 层 B 请求误走层 C `HTTP_PROXY` | 使用 `internal_client`；确认 `NO_PROXY` 含 loopback |
| Go 代理连不上 Cursor / 超时 | 未配翻墙或配错 | 设 `PROXY_UPSTREAM_URL`（不是 `HTTPS_PROXY`） |
| Go 代理启动后请求打回自己 | 对 Go 进程设置了 `HTTPS_PROXY=:8317` | 去掉；上游只用 `PROXY_UPSTREAM_URL` |
| 助手回复「暂时不可用」，日志 `httpx.ProxyError: 403` | 壳层把 `PROXY_PUBLIC_URL`/`:8317` 当成了系统 `HTTPS_PROXY`，LLM 打到 MITM | 不要 `export HTTPS_PROXY=$PROXY_PUBLIC_URL`；或在项目 `.env` 写 `HTTPS_PROXY=` / `HTTP_PROXY=` 清空；`cursor-pulse.sh` 也会自动剥离 MITM 地址 |
| Docker proxy 拉不到池 / authorize 失败 | `PULSE_BASE_URL=http://web:8080` 在独立网络无效 | 用 `PROXY_PULSE_BASE_URL=http://host.docker.internal:8080` |

更多运维细节见 [proxy/README.md](../proxy/README.md)、[RUNBOOK.md](RUNBOOK.md)。
