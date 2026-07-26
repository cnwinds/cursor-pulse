# 架构说明

Cursor Pulse 是**自托管 monorepo**：Cursor 控制面 + 可选 IM + 可选 Assistant + 可选数据面（MITM Proxy）。

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  web-admin  │────▶│  Pulse web API   │◀────│ Channel runtime │
│  (Vue SPA)  │     │  + sync / loans  │     │ none/dingtalk/  │
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

身份模型：`Member` 表示人（台账 / 门户角色 / 密码）；`MemberIdentity`（`channel` + `external_id`）表示登录与 IM 寻址键。一人可绑定 web / 钉钉 / 飞书 多身份。业务逻辑通过 `resolve_member` 查找，不直接依赖某一 IM SDK。

## 进程

| 进程 | 启动方式 | 默认端口 | 职责 |
|------|----------|----------|------|
| Pulse web | `pulse web` | `:8080` | 门户 JWT API、内部 Provider、静态管理后台 |
| Channel | `pulse channel` | — | 可选 IM 入站 + **Cursor 同步 tick / 借 Key 过期**；`BOT_PLATFORM=none` 时仅调度 |
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

`create_messenger` / `create_runtime` 按 `bot.name` / `BOT_PLATFORM` 选择实现。IM **不**用于用量 CSV/截图采集。

## 数据库

| 位置 | 归属 |
|------|------|
| `data/pulse.db`（或 `DATABASE_URL`） | Pulse 控制面 |
| `data/assistant.db`（`ASSISTANT_DATABASE_URL`） | Assistant |

## 用量（仅 Cursor）

1. 管理后台创建 Cursor 台账账号并指定主使用人  
2. 绑定 User API Key（加密存储）  
3. `pulse channel` 定时 `cursor_sync` 拉取用量事件并刷新额度快照  
4. 可选：借 Key（`pka_` 代理别名）与 Go Proxy 上报  

不支持其他 AI 厂商台账，也不接受 CSV / 截图 / 文本手工上报。

## HTTP 面

**门户（JWT / 门户登录）**

- `/api/auth/providers` — 可用登录方式（password / dingtalk_oauth / feishu_oauth）
- `/api/auth/*`、`/api/v2/*`（账号、凭证、额度、借贷、Proxy Key、Assistant 代理等）
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

**On-Demand 强制关闭（Cursor 同步）：** 团队级开关 `cursor_sync.enforce_on_demand_disabled`（默认 true），可在 Web 系统设置编辑；见 [cursor-usage-api.md](cursor-usage-api.md) 与 [RUNBOOK.md](RUNBOOK.md)。

**Assistant**

- `/api/assistant/v1/*`；生产通常经 Pulse 门户代理。

## 目录

| 路径 | 说明 |
|------|------|
| `pulse/` | 控制面 Python 包 |
| `assistant_platform/` | Assistant（同 wheel，进程可分离） |
| `proxy/` | Go 模块（默认 Docker 镜像不含） |
| `web-admin/` | Vue 管理后台 |
| `docker/` | 正式 compose / Dockerfile |
| `docs/` | 架构 / 运维 / API 笔记 |
