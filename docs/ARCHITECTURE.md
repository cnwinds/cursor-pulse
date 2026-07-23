# 架构说明

Cursor Pulse 是**自托管 monorepo**，含三套运行时与可选数据面。

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  web-admin  │────▶│  Pulse web API   │◀────│  钉钉 Stream │
│  (Vue SPA)  │     │  + channel 机器人 │     │             │
└─────────────┘     └────────┬─────────┘     └─────────────┘
                             │ 内部 HTTP
                    ┌────────▼─────────┐
                    │ Assistant 服务    │  （可选进程）
                    └──────────────────┘
                             ▲
                    ┌────────┴─────────┐
                    │ Go MITM 代理      │  （可选数据面）
                    └──────────────────┘
```

## 进程

| 进程 | 启动方式 | 默认端口 | 职责 |
|------|----------|----------|------|
| Pulse web | `pulse web` | `:8080` | 门户 JWT API、内部 Provider、静态管理后台 |
| Channel | `pulse channel` | Stream | 钉钉接入、定时任务、能力桥 |
| Assistant | `python -m assistant_platform serve` | `:8090` | 会话 / 技能 / 能力调用（开启镜像时） |
| 管理 UI（开发） | `web-admin` 下 `npm run dev` | `:5173` | Vue 门户 |
| Proxy（可选） | `cursor-pulse start proxy` | `:8317` | HTTPS MITM + 用量上报 |

本地可用 `cursor-pulse.bat` / `.sh` / `.ps1` 统一启停。

## 数据库

| 位置 | 归属 |
|------|------|
| `data/pulse.db`（或 `DATABASE_URL`） | Pulse 控制面 |
| `data/assistant.db`（`ASSISTANT_DATABASE_URL`） | Assistant |

## 用量采集

- **Cursor：** 绑定 User API Key → 定时/按需 API 同步
- **其他工具：** 钉钉手工提交 CSV/XLSX / 截图 / 文本（非 Cursor 主路径）

## HTTP 面（建议对外支持）

**门户（JWT / 门户登录）**

- `/api/auth/*`、`/api/v2/*`（账号、凭证、额度、借贷、Assistant 代理等）
- `/health`

**内部（service token；未配置应失败关闭）**

- `/api/internal/v1/capabilities/*`
- `/api/internal/v1/channel/reply`
- `/api/internal/v1/proxy/{authorize,pool,usage,events}`

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
