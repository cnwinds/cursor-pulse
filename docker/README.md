# Cursor Pulse — Linux Docker 部署

在 Linux 服务器上用 **一条** `docker compose up -d` 跑核心进程（含自动 schema 初始化）：

| 容器 | 命令 | 说明 |
|------|------|------|
| `init-db` | `pulse init-db` | oneshot，每次 up 幂等迁移；成功后退出 |
| `web` | `pulse web` | 管理后台 API + Vue 静态页 `/admin/` |
| `assistant` | `assistant_platform serve` | 会话账本、记忆、能力中心 |
| `channel` | `pulse channel` | 钉钉 Stream + 定时调度 |

**宿主机映射（勿删）：**

| 宿主机 | 容器 | 说明 |
|--------|------|------|
| `docker/data/` | `/app/data` | `pulse.db`、`assistant.db`、`raw/` |
| `docker/config.yaml` | `/app/config/config.yaml`（只读） | 业务配置 |
| `docker/.env` | `/app/.env`（只读）+ `env_file` | 密钥与功能开关；**改完 `restart` 即可** |

可选 **Proxy** 见下文独立 compose，不进入主文件。

---

## 1. 准备服务器

```bash
# 安装 Docker + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # 重新登录生效

git clone <repo-url> /opt/cursor-pulse
cd /opt/cursor-pulse/docker
chmod +x scripts/*.sh
./scripts/setup.sh
```

编辑 `docker/.env`（钉钉凭证、JWT、加密密钥）和 `docker/config.yaml`（收集周期等）。

**生产 OAuth / CORS**（`.env`）：

```env
DINGTALK_OAUTH_REDIRECT_URI=https://your-domain.com/admin/login/callback
WEB_CORS_ORIGINS=https://your-domain.com
```

钉钉开放平台重定向 URL 需与上面一致。

---

## 2. 从本地迁移数据

在**本地 Windows** 先停服务，再打包上传：

```powershell
# 本地
.\cursor-pulse.ps1 stop
```

在 Linux 服务器上，从开发机 rsync（推荐）：

```bash
# 在服务器 docker/ 目录
./scripts/migrate-data.sh your-user@dev-pc:/path/to/cursor-pulse/data
```

或手动 scp：

```bash
# 本地 PowerShell / Git Bash
scp -r data/pulse.db data/assistant.db data/raw \
  user@server:/opt/cursor-pulse/docker/data/
```

**加密密钥必须与本地一致**，否则无法解密已存数据：

- `PULSE_CREDENTIAL_ENCRYPTION_KEY` — Cursor API Key
- `ASSISTANT_SECRET_KEY` — Assistant 密钥库

---

## 3. 构建与启动

```bash
cd /opt/cursor-pulse/docker

# 构建并启动全部核心服务（自动跑 init-db）
docker compose up -d --build

# 查看状态
docker compose ps
docker compose logs -f web channel assistant
```

访问：`http://<服务器IP>:8080/admin/`（或经宿主机 Nginx 反代 HTTPS）。

健康检查：

```bash
curl -s http://127.0.0.1:8080/health
# assistant 默认不映射宿主机端口；排查时可临时 ports 暴露 8090
```

升级镜像后同样：

```bash
docker compose up -d --build
```

仅排查时手动重跑迁移：

```bash
docker compose run --rm init-db
```

初始化 v2 工具中心表（可选，非默认 up 路径）：

```bash
docker compose --profile tools run --rm init-v2
```

---

## 4. 常用运维命令

```bash
# 停止
docker compose down

# 备份 SQLite（宿主机文件）
./scripts/backup-data.sh

# 进入容器执行 CLI
docker compose exec web pulse admin bootstrap --user-id <userid> --password <pwd>
docker compose exec web pulse remind report --period 2026-06
```

---

## 5. Nginx 反向代理（宿主机，可选）

主 compose **不含** Nginx。示例：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 6. 可选：独立 Proxy

Go MITM 数据面**单独**编排，不随主 `up` 启动：

```bash
cd /opt/cursor-pulse/docker
# .env 中需有 PULSE_INTERNAL_SERVICE_TOKEN
# 默认 PULSE_BASE_URL=http://host.docker.internal:8080（访问宿主机映射的 web）
docker compose -f docker-compose.proxy.yml up -d --build
```

- 端口：`${PULSE_PROXY_PORT:-8317}`
- 控制面地址：默认 `PROXY_PULSE_BASE_URL=http://host.docker.internal:8080`（勿用主栈的 `http://web:8080`）
- CA / 状态目录：宿主机 `docker/proxy-data/` → 容器 `/data`（`HOME=/data`）
- 详见 [proxy/README.md](../proxy/README.md)

---

## 7. 可选：Postgres

默认 SQLite（`data/pulse.db` + `data/assistant.db`），小团队足够。

若 Pulse 主库要用 Postgres：

```bash
# .env 中设置 POSTGRES_PASSWORD
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

注意：`assistant.db` 默认仍为 SQLite；从 SQLite 迁 Postgres 需自行导入，无内置一键工具。

---

## 8. 目录结构

```
docker/
├── Dockerfile
├── Dockerfile.proxy
├── docker-compose.yml
├── docker-compose.proxy.yml      # 可选 Proxy
├── docker-compose.postgres.yml   # 可选 Postgres
├── .env.example
├── config.yaml                   # 从 setup.sh 生成，勿提交 Git
├── .env                          # 勿提交 Git
├── data/                         # 持久化数据（勿提交 Git）
│   ├── pulse.db
│   ├── assistant.db
│   └── raw/
├── proxy-data/                   # Proxy CA 等（勿提交 Git）
├── scripts/
│   ├── setup.sh
│   ├── migrate-data.sh
│   └── backup-data.sh
└── README.md
```

---

## 9. 注意事项

1. **单实例 channel** — 同一钉钉机器人不能多容器抢 Stream 连接。
2. **先停再拷库** — 迁移时避免 `-wal`/`-shm` 不一致；脚本会尝试 `sqlite3 .backup`。
3. **Assistant 自动迁移** — `assistant` 容器启动时会执行 `init_assistant_db`（补表/补列）。
4. **功能开关** — LLM / 搜索等见 `.env.example`，不通过拆 compose 控制。
5. **根目录旧 compose** — 若仓库根部仍有早期单容器版，生产请只用本目录。
