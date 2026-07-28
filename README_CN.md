<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/logo-horizontal-dark.svg" />
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/logo-horizontal.svg" />
    <img src="docs/assets/brand/logo-horizontal.svg" alt="Cursor Pulse" width="380" />
  </picture>
</p>

# Cursor Pulse

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](docker/)
[![GitHub release](https://img.shields.io/github/v/release/cnwinds/cursor-pulse)](https://github.com/cnwinds/cursor-pulse/releases)
[![GitHub stars](https://img.shields.io/github/stars/cnwinds/cursor-pulse?style=social&label=Star)](https://github.com/cnwinds/cursor-pulse/stargazers)

自托管的 **Cursor 用量计量与额度控制面**：账号台账、API Key 同步、借 Key、额度看板与可选 MITM Proxy。核心能力只需 Web + 数据库；**钉钉 / 飞书等 IM 为可选插件**（不承载用量采集）。

> **许可证：** [MIT](LICENSE) · **声明：** [NOTICE](NOTICE) · **安全：** [SECURITY.md](SECURITY.md) · **贡献：** [CONTRIBUTING.md](CONTRIBUTING.md) · **English：** [README.md](README.md)

## 快速预览

登录 → 建台账绑 Key → 看板出数据 → 借 Key（约 20 秒，界面为产品示意，以你部署的实例为准）：

<!-- 内嵌演示：裸写的 user-attachments 链接会被 GitHub 渲染成原生播放器
     （居中大播放按钮，原地播放）。视频托管在 GitHub CDN，不占仓库体积。
     源文件见 promotion/video/（1080p 母版与 720p 网页版）。 -->
https://github.com/user-attachments/assets/38951573-7f9c-4771-88c7-51716933ba0a

<details>
  <summary>无法播放视频？查看 GIF 版本</summary>
  <p align="center">
    <img src="docs/assets/readme/demo.gif" alt="Cursor Pulse 演示：登录、台账、看板、借 Key" width="960" />
  </p>
</details>

<table>
  <tr>
    <td align="center" width="33%">
      <a href="docs/assets/readme/quota-board.png">
        <img src="docs/assets/readme/quota-board.png" alt="额度看板" width="100%" />
      </a><br />
      <sub><b>额度看板</b> — 与 Cursor Plan &amp; Usage 对齐</sub>
    </td>
    <td align="center" width="33%">
      <a href="docs/assets/readme/accounts-bind-key.png">
        <img src="docs/assets/readme/accounts-bind-key.png" alt="账号台账与 Key 绑定" width="100%" />
      </a><br />
      <sub><b>账号台账</b> — 绑定 Cursor User API Key</sub>
    </td>
    <td align="center" width="33%">
      <a href="docs/assets/readme/key-loan.png">
        <img src="docs/assets/readme/key-loan.png" alt="借 Key" width="100%" />
      </a><br />
      <sub><b>借 Key</b> — 成员自助或管理员代分配</sub>
    </td>
  </tr>
</table>

## 它适合谁 / 不适合谁

### 适合

- **小团队 / 工作室** 有多把 Cursor 账号，需要统一看额度、谁在用、还剩多少
- **愿意自托管** 的团队：数据落在自己的服务器 / Docker，而不是第三方 SaaS
- **有借 Key 需求**：临时把账号额度借给同事或外包，到期自动回收
- **可选代理场景**：需要 MITM Proxy 做透明轮换与用量归因（接受 CA 与合规风险）
- **已有运维习惯**：会配 `.env`、备份 SQLite/Postgres、按需接钉钉 / 飞书

### 不适合

- **只想个人用 Cursor、单账号** — 直接用 Cursor 官方后台即可，不必上控制面
- **不愿维护服务器** — 需要 Docker 或 Python 进程长期运行与同步任务
- **强合规禁止 MITM** 的环境 — Proxy 需终端信任自签 CA，请先看 [proxy/README.md](proxy/README.md)
- **依赖 Cursor 未公开 API 做关键业务** — 同步接口可能随 Cursor 升级失效（见 [docs/cursor-usage-api.md](docs/cursor-usage-api.md)）
- **需要完整 Cursor 账号生命周期替代** — 本产品是计量与借还，不是 Cursor 账号批发商

诚实讲清边界，比过度承诺更能减少踩坑。

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
git clone https://github.com/cnwinds/cursor-pulse.git
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
| [README.md](README.md) | English README |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 运维：Docker 细节、备份、IM、升级、故障 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 进程与 API 面 |
| [docs/bot-commands.md](docs/bot-commands.md) | 可选 IM 机器人命令 |
| [docs/PROXY_LAYERS.md](docs/PROXY_LAYERS.md) | 三类「代理」勿混淆 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [ROADMAP.md](ROADMAP.md) | 公开路线图 |
| [docs/cursor-usage-api.md](docs/cursor-usage-api.md) | Cursor 非官方 API 笔记（可能失效） |
| [proxy/README.md](proxy/README.md) | MITM 代理（CA / 合规风险） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献与测试门禁 |
| [SECURITY.md](SECURITY.md) | 漏洞报告与密钥处理 |

## 风险说明

代理与 Cursor 非官方 API 均有合规与失效风险；生产使用前请自行评估。详见 [SECURITY.md](SECURITY.md) 与 [proxy/README.md](proxy/README.md)。

本项目的发布目的是让团队对**自有** Cursor 用量进行合法的内部计量。严禁利用本项目（原版或修改版）转售 Cursor 账号、订阅、API 密钥或额度，或运营任何收费性质的账号共享服务；任何部署与运营行为的法律责任由运营者自行承担，作者不承担连带责任。详见 [NOTICE](NOTICE)。

---

## Star History

<a href="https://star-history.com/#cnwinds/cursor-pulse&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=cnwinds/cursor-pulse&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=cnwinds/cursor-pulse&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=cnwinds/cursor-pulse&type=Date" width="600" />
  </picture>
</a>
