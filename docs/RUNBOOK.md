# 运维手册

面向自托管部署的管理员。时区默认与 `config.yaml` → `collection.timezone` 一致（示例为 `Asia/Shanghai`）。

## 1. 进程与数据

| 组件 | 命令 | 说明 |
|------|------|------|
| 机器人 + 调度 | `pulse channel` | 钉钉 Stream；勿多实例抢同一机器人 |
| 管理 API | `pulse web` | JWT / 钉钉扫码；默认 `:8080` |
| Vue（开发） | `web-admin` → `npm run dev` | `:5173`，代理 `/api` |
| Vue（生产） | `npm run build` 后由 web 挂载 `/admin/` | |
| 可选 Assistant | `python -m assistant_platform serve` | `:8090` |
| 可选 Proxy | `cursor-pulse start proxy` | `:8317` |
| 数据库 | `data/pulse.db` 或 Postgres | |
| 原始文件 | `data/raw/` | 手工提交的附件等 |

本地一键：`.\cursor-pulse.bat start` / `./cursor-pulse.sh start`。架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 2. 部署

### 2.1 钉钉（一次性）

1. 创建**企业内部应用**，启用机器人，模式选 **Stream**。
2. 开通收发消息、媒体下载等权限；记录 AppKey / AppSecret / robot_code。
3. 机器人入群；配置 `DINGTALK_GROUP_ID`（openConversationId），或群内 @ 一次自动绑定。

### 2.2 配置

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

生产至少配置：

| 变量 | 说明 |
|------|------|
| `DINGTALK_APP_KEY` / `SECRET` / `ROBOT_CODE` | 钉钉凭证 |
| `DINGTALK_ADMIN_USER_IDS` | 管理员 userid（逗号分隔） |
| `JWT_SECRET` | Web JWT（生产必配） |
| `PULSE_CREDENTIAL_ENCRYPTION_KEY` | 凭证加密 |
| `PULSE_INTERNAL_SERVICE_TOKEN` | 内部 API（与 Assistant / Proxy 共用约定） |
| `ASSISTANT_SERVICE_TOKEN` / `ASSISTANT_SECRET_KEY` | 启用 Assistant 时 |

更多变量见 `.env.example`。安全要求见 [../SECURITY.md](../SECURITY.md)。

### 2.3 Docker（推荐）

只在 `docker/` 目录操作，详见 [../docker/README.md](../docker/README.md)：

```bash
cd docker
./scripts/setup.sh
# 编辑 .env（钉钉等）
docker compose up -d --build   # init-db 自动执行；data/config 映射到宿主机
```

可选 Proxy：`docker compose -f docker-compose.proxy.yml up -d --build`。  
Postgres：叠加 `docker-compose.postgres.yml`，并设置 `DATABASE_URL`（非默认）。

### 2.4 裸机

```bash
pip install -e ".[web]"          # 按需加 pdf,postgres,s3
pulse init-db
pulse web --host 0.0.0.0 --port 8080
pulse channel                    # 另一进程
```

## 3. 日常操作

```bash
# 调度 / 催办（示例）
pulse remind start
pulse remind daily
pulse remind report --period 2026-06

# 聚合 / 月报 / 导出
pulse aggregate --period 2026-06
pulse report --period 2026-06 --publish
pulse export --period 2026-06 -o data/raw/export_2026-06.csv

# 告警 / BI
pulse alerts --period 2026-06
pulse bi-push --period 2026-06

# 门户管理员
pulse admin bootstrap --user-id <钉钉userid> --name "管理员" --password <密码>
```

钉钉命令说明：[bot-commands.md](bot-commands.md)。

非 Cursor 工具补录：

```bash
pulse import /path/to/usage-events.csv --user-id <userid> --name Alice --period 2026-06
```

## 4. 健康检查

- 进程：`cursor-pulse status` 或系统服务状态
- HTTP：`GET /health`（web / assistant）
- 冒烟：私聊「帮助」、绑定 Key、管理后台登录

## 5. 备份与恢复

备份：`data/`（或 Postgres dump）、`config.yaml`、加密密钥材料（自行保管，勿入库）。

SQLite 示例：

```bash
sqlite3 data/pulse.db ".backup data/pulse-backup.db"
```

恢复：停服务 → 换回文件 / 还原库 → 确认密钥与配置一致 → 再启动。

## 6. 升级

```bash
git pull
pip install -e ".[web]"
# Docker: cd docker && docker compose build && docker compose up -d
pulse init-db   # 或 docker compose --profile tools run --rm init-db
```

回滚：切回上一 tag，必要时恢复数据库备份。

## 7. 故障速查

| 现象 | 排查 |
|------|------|
| 机器人无响应 | Stream 是否在跑；AppKey/Secret/robot_code；是否被踢出群 |
| 群发失败 | openConversationId 是否绑定；机器人权限 |
| Web 401 | `JWT_SECRET`、管理员角色、OAuth 回调 |
| Internal 503 | 未配置 `PULSE_INTERNAL_SERVICE_TOKEN` |
| 启动拒绝 change-me | 重新跑 `docker/scripts/setup.sh` 或手写高熵令牌 |

日志：开发态见 `.dev/logs/`；生产见容器 / systemd 日志。

## 8. CLI 速查

| 命令 | 作用 |
|------|------|
| `pulse init-db` / `init-v2 --seed` | 建库 / v2 种子 |
| `pulse channel` | 渠道 + 调度 |
| `pulse web` | 控制面 HTTP |
| `pulse aggregate` / `report` / `export` | 聚合 / 月报 / 导出 |
| `pulse admin bootstrap\|grant\|revoke` | 门户账号 |
| `pulse reprice` / `reprice-proxy` | 计价重算（进阶） |
