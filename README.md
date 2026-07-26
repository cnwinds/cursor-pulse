# Cursor Pulse

自托管的 **Cursor 用量计量与额度控制面**：账号台账、API Key 同步、借 Key、额度看板与可选 MITM Proxy。核心能力仅需 Web + 数据库；**钉钉 / 飞书等 IM 渠道为可选插件**（不承载用量采集）。

> **许可证：** [MIT](LICENSE) · **安全：** [SECURITY.md](SECURITY.md) · **贡献：** [CONTRIBUTING.md](CONTRIBUTING.md)

## 组成

| 层 | 作用 |
|----|------|
| **Pulse**（`pulse/`） | 控制面：台账、Cursor API 同步、借 Key、Web API、可选 IM |
| **Assistant**（`assistant_platform/`） | 可选：会话 / 能力 / 记忆服务 |
| **管理后台**（`web-admin/`） | Vue 门户（开发用 Vite，或构建后由 Pulse web 托管） |
| **Proxy**（`proxy/`） | 可选：Go HTTPS MITM，截获 Cursor 流量并上报用量 |

用量只通过 **绑定 Cursor User API Key → 自动同步**。本项目不提供 CSV / 截图 / 其他 AI 工具台账。

## 快速开始（Web-only，无钉钉/飞书）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"
# 可选 IM：pip install -e ".[dingtalk]" 或 ".[feishu]"

cp config.example.yaml config.yaml
cp .env.example .env
# 最少填写：ADMIN_PASSWORD、JWT_SECRET、PULSE_CREDENTIAL_ENCRYPTION_KEY
# BOT_PLATFORM=none（默认）即可只用管理后台

pulse init-db   # 建表 + 预置 Cursor 套餐/试用账号（可用 --no-seed 跳过）
pulse admin bootstrap --user-id admin --name "管理员" --password '<密码>' --channel web
pytest --tb=short -q
```

启动最小栈：

```bash
# Windows
.\cursor-pulse.bat start web admin
# macOS/Linux
./cursor-pulse.sh start web admin
```

- API：`http://127.0.0.1:8080`
- 管理 UI（Vite）：`cd web-admin && npm install && npm run dev` → `http://127.0.0.1:5173`
- 登录：本地密码（`admin` + `ADMIN_PASSWORD`）

调度（Cursor 同步 / 借 Key 过期）另开：`pulse channel`。完整栈见 [docs/RUNBOOK.md](docs/RUNBOOK.md)、[proxy/README.md](proxy/README.md)。可选 IM 命令见 [docs/bot-commands.md](docs/bot-commands.md)。

### 可选 IM 渠道

| `BOT_PLATFORM` | 说明 |
|----------------|------|
| `none` | 仅 Web + 调度；出站提醒记日志不发送 |
| `dingtalk` | 钉钉 Stream（`DINGTALK_*`，需 `pip install 'cursor-pulse[dingtalk]'`） |
| `feishu` | 飞书 WebSocket（`FEISHU_*`，需 `pip install 'cursor-pulse[feishu]'`） |

门户登录方式由 `/api/auth/providers` 按凭证动态暴露：未配 IM 时只显示本地密码；配置了钉钉/飞书应用凭证后才出现对应扫码登录。

## Docker

生产编排只在 **`docker/`** 目录（库与配置 bind mount 到 `docker/data`、`docker/config.yaml`）：

```bash
cd docker
./scripts/setup.sh          # 生成 .env / config.yaml，随机 JWT / 加密密钥 / ADMIN_PASSWORD
# 查看 docker/.env 中的 ADMIN_PASSWORD；IM 凭证可选
docker compose up -d --build                    # Web-only：init-db + web + channel
docker compose --profile full up -d --build     # 含 Assistant
```

默认 `BOT_PLATFORM=none`，channel 只跑 Cursor 同步与借 Key 过期。可选 Proxy：`docker compose -f docker-compose.proxy.yml up -d --build`。详情见 [docker/README.md](docker/README.md)。

## 从钉钉-only 升级

| 旧 | 新 |
|----|----|
| `Member.dingtalk_user_id` | `Member.channel` + `Member.channel_user_id` |
| `admin.dingtalk_user_ids` / `DINGTALK_ADMIN_USER_IDS` | `admin.channel_user_ids` / `ADMIN_CHANNEL_USER_IDS` |
| 强制 `BOT_PLATFORM=dingtalk` | 可选 `none` / `dingtalk` / `feishu` |

启动时 `pulse init-db` / `migrate_schema` 会硬切迁移旧列。旧 JWT 字段名失效，用户需重新登录。详见 [docs/RUNBOOK.md](docs/RUNBOOK.md)「身份字段迁移」。

## 文档

| 文档 | 读者 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献者 |
| [SECURITY.md](SECURITY.md) | 漏洞报告与密钥处理 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 进程与 API 面 |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 运维 |
| [docs/bot-commands.md](docs/bot-commands.md) | 可选 IM 机器人命令 |
| [docs/cursor-usage-api.md](docs/cursor-usage-api.md) | Cursor 非官方 API 笔记（可能随时失效，自负风险） |
| [docs/README.md](docs/README.md) | 文档索引 |
| [proxy/README.md](proxy/README.md) | MITM 代理（CA / 合规风险） |

## 风险说明

代理与 Cursor 非官方 API 均有合规与失效风险；生产使用前请自行评估。详见 SECURITY 与 proxy 文档。
