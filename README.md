# Cursor Pulse

自托管的 **Cursor 用量计量与额度控制面**：账号台账、API Key 同步、借 Key、额度看板与可选 MITM Proxy。核心能力只需 Web + 数据库；**钉钉 / 飞书等 IM 为可选插件**（不承载用量采集）。

> **许可证：** [MIT](LICENSE) · **安全：** [SECURITY.md](SECURITY.md) · **贡献：** [CONTRIBUTING.md](CONTRIBUTING.md)

## 组成

| 层 | 作用 |
|----|------|
| **Pulse**（`pulse/`） | 控制面：台账、Cursor API 同步、借 Key、Web API、可选 IM |
| **Assistant**（`assistant_platform/`） | 可选：会话 / 能力 / 记忆服务 |
| **管理后台**（`web-admin/`） | Vue 门户（开发用 Vite；生产由 Pulse web 托管） |
| **Proxy**（`proxy/`） | 可选：Go HTTPS MITM，截获 Cursor 流量并上报用量 |

用量只通过 **绑定 Cursor User API Key → 自动同步**。

## 选哪种启动方式？

| 模式 | 适合谁 | 特点 |
|------|--------|------|
| **Docker 生产** | 服务器上长期自托管，不想装 Python / Node | 一条 `compose` 起服务；数据与配置在 `docker/` |
| **本地开发** | 改代码、跑测试、给项目提 PR | 本机 venv + 脚本；Vite 热更新 |

只想先跑起来用 → **Docker**。要贡献代码 → **本地开发**。

---

## 1. Docker 生产启动

需要：Docker + Compose plugin、一台 Linux 主机（或本机 Docker）。

```bash
git clone <repo-url> cursor-pulse
cd cursor-pulse/docker
chmod +x scripts/*.sh
./scripts/setup.sh          # 生成 .env / config.yaml，并写入随机密钥与 ADMIN_PASSWORD
```

`setup.sh` 已写好启动所需密钥。打开 `docker/.env`，**至少核对并备份**：

```env
BOT_PLATFORM=none
ADMIN_PASSWORD=<setup 已生成，请记下>
JWT_SECRET=<已生成>
PULSE_CREDENTIAL_ENCRYPTION_KEY=<已生成>
```

Web-only 到此即可；钉钉 / 飞书等可以后再配（见 [docs/RUNBOOK.md](docs/RUNBOOK.md)）。

```bash
docker compose up -d --build
```

- 管理后台：`http://<主机IP>:8080/admin/`
- 登录：用户名 `admin`，密码为 `.env` 里的 `ADMIN_PASSWORD`
- 健康检查：`curl -s http://127.0.0.1:8080/health`

默认栈：`init-db` + `web` + `channel`（含 Cursor 同步与借 Key 过期）。含 Assistant：`docker compose --profile full up -d --build`。备份、迁移、Nginx、Postgres、Proxy 等见 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

---

## 2. 本地开发启动

需要：Python ≥ 3.11；改前端时再装 Node 20+。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"

cp config.example.yaml config.yaml
cp .env.example .env
```

编辑根目录 `.env`，**最少填写**（开发也勿用空密钥）：

```env
BOT_PLATFORM=none
ADMIN_PASSWORD=<自设强密码>
JWT_SECRET=<高熵字符串，建议 ≥32 字节>
PULSE_CREDENTIAL_ENCRYPTION_KEY=<高熵字符串>
```

```bash
pulse init-db
pulse admin bootstrap --user-id admin --name "管理员" --password '<与 ADMIN_PASSWORD 相同>' --channel web

# Windows
.\cursor-pulse.bat start web admin
# macOS/Linux
./cursor-pulse.sh start web admin
```

- API：`http://127.0.0.1:8080`
- 管理 UI（Vite）：`http://127.0.0.1:5173`（脚本会起 `admin`；也可 `cd web-admin && npm install && npm run dev`）
- 登录：`admin` + `ADMIN_PASSWORD`

用量同步与借 Key 过期需要 `pulse channel`。开发时另开终端：`pulse channel`，或用完整启动：`./cursor-pulse.sh start`（见脚本帮助）。测试：`pytest --tb=short -q`（更快：`pytest -n auto --tb=short -q`）。

可选 IM：`pip install -e ".[dingtalk]"` 或 `".[feishu]"`，并将 `BOT_PLATFORM` 设为对应值。门户会按凭证动态露出扫码登录（`/api/auth/providers`）。

---

## 3. 新手教程（启动成功后）

两种模式共用。目标：绑定一把 Cursor Key，并看到用量开始进系统。

1. **登录管理后台**  
   Docker：`http://<主机>:8080/admin/` · 开发：`http://127.0.0.1:5173`  
   账号 `admin` + 你的 `ADMIN_PASSWORD`。

2. **创建本地用户**（可选，给团队成员用）  
   **用户管理 → 创建用户**，设好用户名与密码。

3. **建 Cursor 台账并绑定 Key**  
   在台账里创建 Cursor 账号，指定负责人（自己或刚建的用户），绑定 **Cursor User API Key**（在 Cursor 账户设置中创建）。

4. **确认同步**  
   确保 `channel` 在跑（Docker 默认已起）。稍等一个同步周期后，额度 / 用量看板应有数据。若一直为空，见 [docs/RUNBOOK.md](docs/RUNBOOK.md)「故障速查」。

5. **借 Key（可选）**  
   成员可在 **我的借用** 自助申请临时 Key；管理员可在 **借用记录** 代分配。

更细的运维、IM、架构与代理说明见下方文档。

---

## 文档

| 文档 | 内容 |
|------|------|
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 运维：Docker 细节、备份、IM、升级、故障 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 进程与 API 面 |
| [docs/bot-commands.md](docs/bot-commands.md) | 可选 IM 机器人命令 |
| [docs/PROXY_LAYERS.md](docs/PROXY_LAYERS.md) | 三类「代理」勿混淆 |
| [docs/cursor-usage-api.md](docs/cursor-usage-api.md) | Cursor 非官方 API 笔记（可能失效） |
| [proxy/README.md](proxy/README.md) | MITM 代理（CA / 合规风险） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献与测试门禁 |
| [SECURITY.md](SECURITY.md) | 漏洞报告与密钥处理 |

## 风险说明

代理与 Cursor 非官方 API 均有合规与失效风险；生产使用前请自行评估。详见 [SECURITY.md](SECURITY.md) 与 [proxy/README.md](proxy/README.md)。
