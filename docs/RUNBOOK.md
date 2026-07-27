# 运维手册

面向自托管管理员。最短启动路径见[根 README](../README.md)；本文是备份、可选组件与排障。

时区默认与 `config.yaml` → `collection.timezone` 一致（示例 `Asia/Shanghai`）。本项目只服务 **Cursor**。

## 1. 进程与数据

| 组件 | 命令 | 说明 |
|------|------|------|
| 管理 API | `pulse web` | JWT / 本地密码 / 可选 IM 扫码；默认 `:8080` |
| 渠道 + 调度 | `pulse channel` | Cursor 同步 + 借 Key 过期；IM 时另含入站 |
| Vue（开发） | `web-admin` → `npm run dev` | `:5173` |
| Vue（生产） | 构建后由 web 挂载 `/admin/` | |
| 可选 Assistant | `python -m assistant_platform serve` | `:8090` |
| 可选 Proxy | 见下文 Docker Proxy | `:8317` |
| 数据库 | `data/pulse.db` 或 Postgres | |

架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。三类代理见 [PROXY_LAYERS.md](PROXY_LAYERS.md)。

## 2. 环境变量（最少集）

| 变量 | 说明 |
|------|------|
| `ADMIN_PASSWORD` | 本地超管密码（Web-only 必配） |
| `JWT_SECRET` | Web JWT（生产 ≥32 字节） |
| `PULSE_CREDENTIAL_ENCRYPTION_KEY` | Cursor Key 等凭证加密 |
| `BOT_PLATFORM` | `none` / `dingtalk` / `feishu` |
| `ADMIN_CHANNEL_USER_IDS` | 渠道管理员 userid（IM 用，可空） |
| `PULSE_INTERNAL_SERVICE_TOKEN` | Pulse / Proxy 内部 API |
| `PULSE_INTERNAL_TOKEN` | Assistant→Pulse（通常与上项同值） |
| `PULSE_BASE_URL` | Assistant / Proxy 访问 Pulse 的 URL |

完整列表见仓库根 `.env.example`、`docker/.env.example` 与 [SECURITY.md](../SECURITY.md)。

`ADMIN_PASSWORD` 仅用于首次 bootstrap / 超管 `admin`；首次成功登录后写入库内 `password_hash`，之后以库为准。

## 3. Docker 运维

生产编排只在 **`docker/`**（数据与配置 bind mount 到 `docker/data`、`docker/config.yaml`）。启动步骤见[根 README](../README.md)。

| 容器 | 说明 |
|------|------|
| `init-db` | oneshot：建表 + seed；成功后退出 |
| `web` | 管理 API + `/admin/` 静态页 |
| `channel` | 调度；`BOT_PLATFORM=none` 时无 IM |
| `assistant` | 可选：`--profile full` |

宿主机映射：`docker/data/` → `/app/data`；`docker/config.yaml`、`docker/.env` 只读挂载。改 `.env` 后一般 `docker compose restart` 即可。

### 常用命令

```bash
cd docker
docker compose ps
docker compose logs -f web channel
docker compose down
./scripts/backup-data.sh
docker compose exec web pulse admin bootstrap --user-id <id> --password <pwd>
# 排查迁移
docker compose run --rm init-db
```

升级：

```bash
git pull
cd docker && docker compose up -d --build
```

### 从开发机迁移数据

先停本地服务，再打包。加密密钥须与源环境一致（`PULSE_CREDENTIAL_ENCRYPTION_KEY`、若开 Assistant 则还有 `ASSISTANT_SECRET_KEY`）。

```bash
# 在服务器 docker/ 目录
./scripts/migrate-data.sh your-user@dev-pc:/path/to/cursor-pulse/data
```

或手动拷贝 `pulse.db` / `assistant.db` / `raw/` 到 `docker/data/`。迁移时避免 `-wal`/`-shm` 不一致；脚本会尝试 `sqlite3 .backup`。

### Nginx（可选）

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

启用 IM 扫码登录时，在 `docker/.env` 设置：

```env
DINGTALK_OAUTH_REDIRECT_URI=https://your-domain.com/admin/login/callback
WEB_CORS_ORIGINS=https://your-domain.com
```

### 可选：Proxy

```bash
cd docker
# .env 中需有 PULSE_INTERNAL_SERVICE_TOKEN
docker compose -f docker-compose.proxy.yml up -d --build
```

- 端口：`${PULSE_PROXY_PORT:-8317}`（改端口需 `up -d` 重建）
- 控制面默认 `PROXY_PULSE_BASE_URL=http://host.docker.internal:8080`（勿用主栈的 `http://web:8080`）
- CA：`docker/proxy-data/` → 容器 `/data`
- 详见 [proxy/README.md](../proxy/README.md)、[PROXY_LAYERS.md](PROXY_LAYERS.md)

### 可选：Postgres

默认 SQLite（`data/pulse.db`）。改用 Postgres：

```bash
# .env 中设置 POSTGRES_PASSWORD 与 DATABASE_URL
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

`assistant.db` 默认仍为 SQLite；从 SQLite 迁 Postgres 需自行导入。

### 注意

1. **单实例 channel** — 同一 IM 机器人不要多容器抢连接。
2. **功能开关**（LLM / 搜索等）见 `.env.example`，不靠拆 compose。
3. 开发改代码请用本机 venv（见根 README）；生产镜像把代码打进镜像，改代码需 rebuild。

## 4. 可选 IM

| `BOT_PLATFORM` | 做法 |
|----------------|------|
| `dingtalk` | `pip install 'cursor-pulse[dingtalk]'`（Docker 镜像已含）；填 `DINGTALK_*` |
| `feishu` | `pip install 'cursor-pulse[feishu]'`；填 `FEISHU_*` |

命令说明：[bot-commands.md](bot-commands.md)。

门户身份路径：

| 路径 | 做法 |
|------|------|
| 纯 Web | 创建本地用户 → 挂台账 → 用户名密码登录 |
| 先本地后 IM | 创建用户并挂台账 → 配 IM → **关联渠道** |
| 先 IM | 通讯录 / OAuth 待审批 → 审批开通 → 挂台账；可「设密码」补 Web 登录 |

## 5. 身份字段迁移（旧钉钉-only）

`pulse init-db` / `migrate_schema` 会硬切迁移旧列：

| 旧 | 新 |
|----|----|
| `admin.dingtalk_user_ids` | `admin.channel_user_ids` |
| `DINGTALK_ADMIN_USER_IDS` | `ADMIN_CHANNEL_USER_IDS` |

升级后请重新登录。新默认 `BOT_PLATFORM=none`；已有钉钉部署须显式 `BOT_PLATFORM=dingtalk`。

## 6. 健康检查与备份

- HTTP：`GET /health`
- 冒烟：登录 → 绑定 Key → 额度看板有数

备份：`data/`（或 Postgres dump）、`config.yaml`、加密密钥（勿入库）。

```bash
sqlite3 data/pulse.db ".backup data/pulse-backup.db"
# Docker
cd docker && ./scripts/backup-data.sh
```

## 7. 凭证加密密钥轮换

1. 停写：`web` / `channel` / Assistant / Proxy  
2. 备份数据库  
3. `pulse rotate-credential-key --old … --new …`（先 `--dry-run`）  
4. 更新 `.env` 中的 `PULSE_CREDENTIAL_ENCRYPTION_KEY`  
5. 重启并抽查绑定 Key / 代理 authorize  

## 8. 故障速查

| 现象 | 排查 |
|------|------|
| 机器人无响应 | Stream；AppKey/Secret；是否在群 |
| Web 401 | `JWT_SECRET`、角色、OAuth 回调 |
| Internal 503 | 未配置 `PULSE_INTERNAL_SERVICE_TOKEN` |
| 同步无数据 | 是否已绑定 Key；`channel` 是否在跑；加密密钥是否与库匹配 |

## 9. CLI 速查

| 命令 | 作用 |
|------|------|
| `pulse init-db` | 建库 + seed Cursor 目录（`--no-seed` 可跳过） |
| `pulse rotate-credential-key` | 轮换凭证加密密钥 |
| `pulse channel` | IM（可选）+ Cursor 同步 / 借 Key 过期 |
| `pulse web` | 控制面 HTTP |
| `pulse admin bootstrap\|grant\|revoke` | 门户账号 |
