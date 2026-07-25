# 架构说明

Cursor Pulse 是**自托管 monorepo**，含控制面、可选 IM 渠道、可选 Assistant 与可选数据面。

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  web-admin  │────▶│  Pulse web API   │◀────│ Channel runtime │
│  (Vue SPA)  │     │  + 调度 / 能力桥  │     │ none/dingtalk/  │
└─────────────┘     └────────┬─────────┘     │ feishu          │
                             │ 内部 HTTP      └─────────────────┘
                    ┌────────▼─────────┐
                    │ Assistant 服务    │  （可选进程）
                    └──────────────────┘
                             ▲
                    ┌────────┴─────────┐
                    │ Go MITM 代理      │  （可选数据面）
                    └──────────────────┘
```

身份模型：`Member.channel`（`web` / `dingtalk` / `feishu`）+ `Member.channel_user_id`。业务逻辑只认这对键，不直接依赖某一 IM SDK。

## 进程

| 进程 | 启动方式 | 默认端口 | 职责 |
|------|----------|----------|------|
| Pulse web | `pulse web` | `:8080` | 门户 JWT API、内部 Provider、静态管理后台 |
| Channel | `pulse channel` | — | IM 入站（可选）+ 定时任务；`BOT_PLATFORM=none` 时仅调度保活 |
| Assistant | `python -m assistant_platform serve` | `:8090` | 会话 / 技能 / 能力调用（开启镜像时） |
| 管理 UI（开发） | `web-admin` 下 `npm run dev` | `:5173` | Vue 门户 |
| Proxy（可选） | `cursor-pulse start proxy` | `:8317` | HTTPS MITM + 用量上报 |

本地可用 `cursor-pulse.bat` / `.sh` / `.ps1` 统一启停。

三类「代理」勿混淆：Cursor MITM、进程内互调、出站翻墙 — 见 [PROXY_LAYERS.md](PROXY_LAYERS.md)。

## 渠道抽象

| 抽象 | 位置 | 职责 |
|------|------|------|
| `ChannelMessenger` | `pulse/channels/base.py` | OTO / 群发 / 文件下载；含 `NullMessenger` |
| `ChannelRuntime` | 同上 | 阻塞入站；含 `NullRuntime` |
| `InboundMessage` + `dispatch_text_command` | `pulse/channels/inbound.py` | 规范化文本命令分发 |
| 钉钉实现 | `pulse/channels/dingtalk/` | Stream + OpenAPI |
| 飞书实现 | `pulse/channels/feishu/` | WebSocket（`lark-oapi`）+ OpenAPI |
| 企微 | `pulse/channels/platforms/wecom.py` | 占位 stub |

`create_messenger` / `create_runtime` 按 `bot.name` / `BOT_PLATFORM` 选择实现。

## 数据库

| 位置 | 归属 |
|------|------|
| `data/pulse.db`（或 `DATABASE_URL`） | Pulse 控制面 |
| `data/assistant.db`（`ASSISTANT_DATABASE_URL`） | Assistant |

## 用量采集

- **Cursor：** 绑定 User API Key → 定时/按需 API 同步（不依赖 IM）
- **其他工具：** 可选经 IM 手工提交 CSV/XLSX / 截图 / 文本

## HTTP 面（建议对外支持）

**门户（JWT / 门户登录）**

- `/api/auth/providers` — 可用登录方式（password / dingtalk_oauth / feishu_oauth）
- `/api/auth/*`、`/api/v2/*`（账号、凭证、额度、借贷、Assistant 代理等）
- `/health`

**内部（service token；未配置应失败关闭）**

- `/api/internal/v1/capabilities/*`
- `/api/internal/v1/channel/reply`
- `/api/internal/v1/proxy/{authorize,pool,usage,events}`

**内部 token 变量名（同一密钥值）：**

| 消费者 | 环境变量 |
|--------|----------|
| Pulse 控制面、Go Proxy | `PULSE_INTERNAL_SERVICE_TOKEN` |
| Assistant → Pulse | `PULSE_INTERNAL_TOKEN` |

Assistant / Proxy 还需 `PULSE_BASE_URL` 指向 Pulse web（如 `http://127.0.0.1:8080`）。

**On-Demand 强制关闭（Cursor 同步）：** 团队级开关 `cursor_sync.enforce_on_demand_disabled`（默认 true），可在 Web 系统设置编辑；见 [cursor-usage-api.md](cursor-usage-api.md#sethardlimit--设置--关闭-on-demand-spending) 与 [RUNBOOK.md](RUNBOOK.md)。

**Assistant**

- `/api/assistant/v1/*`；生产通常经 Pulse 门户代理。

遗留非 v2 的 `/api/*` 仍可能存在，新客户端优先 v2。

## 目录

| 路径 | 说明 |
|------|------|
| `pulse/` | 控制面 Python 包 |
| `assistant_platform/` | Assistant（同 wheel，进程可分离） |
| `proxy/` | Go 模块（默认 Docker 镜像不含） |
| `web-admin/` | Vue 管理后台 |
| `docker/` | 正式 compose / Dockerfile |
| `scripts/` | 辅助脚本 |

Pulse 与 Assistant 目前仍有源码互引，视为同一产品边界。

## 配置

- `config.yaml` ← `config.example.yaml`（非密钥结构）
- `.env` ← `.env.example`（密钥与开关）
- Docker：在 `docker/` 下执行 `scripts/setup.sh` 后编辑 `docker/.env`

切勿提交真实密钥。占位令牌 `change-me-*` 会在 Pulse web 启动时被拒绝。
