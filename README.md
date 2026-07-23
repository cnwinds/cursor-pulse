# Cursor Pulse

自托管的 **AI 工具用量计量与额度控制面**（钉钉优先）：通过 API Key 同步 Cursor 用量，管理账号 / 借 Key / 告警；可选 Assistant 技能会话，以及可选的 Go MITM 代理数据面。

> **许可证：** [MIT](LICENSE) · **安全：** [SECURITY.md](SECURITY.md) · **贡献：** [CONTRIBUTING.md](CONTRIBUTING.md)

## 组成

| 层 | 作用 |
|----|------|
| **Pulse**（`pulse/`） | 控制面：数据库、钉钉渠道、Web API、内部 Provider API |
| **Assistant**（`assistant_platform/`） | 可选：会话 / 能力 / 记忆服务 |
| **管理后台**（`web-admin/`） | Vue 门户（开发用 Vite，或构建后由 Pulse web 托管） |
| **Proxy**（`proxy/`） | 可选：Go HTTPS MITM，截获 Cursor 流量并上报用量 |

Cursor 用量应以 **API Key 自动同步** 为主，不要走 CSV。手工 CSV/XLSX 仅用于非 Cursor 工具。

## 快速开始（本地）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"

cp config.example.yaml config.yaml   # Windows 可用 copy
cp .env.example .env                 # 填写 DINGTALK_* / JWT_SECRET 等

pulse init-db
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
- 管理 UI（Vite）：先 `cd web-admin && npm install && npm run dev`，再打开 `http://127.0.0.1:5173`

完整 channel + assistant（+ proxy）配置见：[docs/bot-commands.md](docs/bot-commands.md)、[docs/RUNBOOK.md](docs/RUNBOOK.md)、[proxy/README.md](proxy/README.md)。

### 钉钉（概要）

1. 在钉钉开放平台创建 **企业内部应用**（这是钉钉的应用类型名称，不是「仅本公司私有代码」的意思）。
2. 启用机器人 + Stream 模式；在 `.env` 中配置 `DINGTALK_APP_KEY` / `SECRET` / `ROBOT_CODE` / 管理员 userid。
3. 运行 `pulse channel`（或 `cursor-pulse start channel`）。

## Docker

生产编排只在 **`docker/`** 目录：

```bash
cd docker
./scripts/setup.sh          # 生成 .env / config.yaml，并写入随机服务令牌
# 编辑 docker/.env — JWT / 加密密钥不可留空
docker compose build
docker compose --profile tools run --rm init-db
docker compose up -d
```

详情见 [docker/README.md](docker/README.md)。

## 文档

| 文档 | 读者 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献者 |
| [SECURITY.md](SECURITY.md) | 漏洞报告与密钥处理 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 进程与 API 面 |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 运维 |
| [docs/bot-commands.md](docs/bot-commands.md) | 钉钉命令 |
| [docs/cursor-usage-api.md](docs/cursor-usage-api.md) | Cursor 非官方 API 笔记（可能随时失效，自负风险） |
| [proxy/README.md](proxy/README.md) | MITM 代理（CA / 合规风险） |

内部设计过程文档在 `docs/superpowers/`（部署不必阅读）。

## 风险说明

可选代理会对 `*.cursor.sh` 做 **TLS MITM**（本地 CA）。非官方 Cursor HTTP 接口可能无通知变更。上线前请自行评估组织策略与 Cursor 服务条款。

## 许可证

[MIT](LICENSE) © 2026 xiongbo
