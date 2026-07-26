# 运维手册

面向自托管部署的管理员。时区默认与 `config.yaml` → `collection.timezone` 一致（示例为 `Asia/Shanghai`）。

## 1. 进程与数据

| 组件 | 命令 | 说明 |
|------|------|------|
| 渠道 + 调度 | `pulse channel` | `BOT_PLATFORM=none` 时仅调度；`dingtalk`/`feishu` 时含 IM 入站（勿多实例抢同一机器人） |
| 管理 API | `pulse web` | JWT / 本地密码 / 可选 IM 扫码；默认 `:8080` |
| Vue（开发） | `web-admin` → `npm run dev` | `:5173`，代理 `/api` |
| Vue（生产） | `npm run build` 后由 web 挂载 `/admin/` | |
| 可选 Assistant | `python -m assistant_platform serve` | `:8090` |
| 可选 Proxy | `cursor-pulse start proxy` | `:8317` |
| 数据库 | `data/pulse.db` 或 Postgres | |
| 原始文件 | `data/raw/` | 手工提交的附件等 |

本地一键：`.\cursor-pulse.bat start` / `./cursor-pulse.sh start`。架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。代理分层（MITM / 内部互调 / 翻墙）见 [PROXY_LAYERS.md](PROXY_LAYERS.md)。

## 2. 部署

### 2.0 Web-only（推荐开源最小形态）

不配任何 IM 也可使用台账、借 Key、额度：

```bash
# .env
BOT_PLATFORM=none
ADMIN_PASSWORD=<强密码>
JWT_SECRET=<高熵>
PULSE_CREDENTIAL_ENCRYPTION_KEY=<高熵>

pulse init-db   # 建表 + seed 厂家/套餐（台账下拉依赖此步）
pulse admin bootstrap --user-id admin --name "管理员" --password '<密码>' --channel web
pulse web
# 调度（Cursor 同步 / 借 Key 过期）另开：pulse channel
```

登录后在 **用户管理 → 创建用户** 添加本地账号（用户名 + 密码），并在台账中选为负责人。  
成员可在 **我的借用** 自助申请临时 Key；管理员可在 **借用记录** 代分配。  
`ADMIN_PASSWORD` 仅用于首次 bootstrap / 超管 `admin`；已设 `password_hash` 的用户以库内密码为准。  
首次用 `ADMIN_PASSWORD` 成功登录后会写入 `admin` 的 `password_hash`，之后以库内密码为准——轮换环境变量不会自动改库，需在用户管理「设密码」或 `pulse admin bootstrap` 更新。

### 2.0b 门户身份三种路径

| 路径 | 做法 |
|------|------|
| 纯 Web | 创建本地用户 → 挂台账 → 用户名密码登录 |
| 先本地后 IM | 创建用户并挂台账 → 配置钉钉/飞书 → **关联渠道** 绑定 IM userid；之后扫码与密码均可登录同一人 |
| 先 IM | 钉钉通讯录同步 / 飞书 OAuth 待审批 → 审批开通 → 可挂台账；可用「设密码」补 Web 登录名 |

一人可有多条 `member_identities`（web / dingtalk / feishu）；台账始终挂 `members.id`，关联渠道不改台账外键。合并冲突（双方都是不同账号主使用人）时需先改台账负责人。

### 2.1 钉钉（可选）

1. `pip install 'cursor-pulse[dingtalk]'`。
2. 创建**企业内部应用**，启用机器人，模式选 **Stream**。
3. 开通收发消息、媒体下载等权限；记录 AppKey / AppSecret / robot_code。
4. 设置 `BOT_PLATFORM=dingtalk` 与 `DINGTALK_*`；机器人入群，配置 `DINGTALK_GROUP_ID` 或群内 @ 一次自动绑定。

### 2.1b 飞书（可选）

1. 创建自建应用，开通机器人与事件订阅（推荐 WebSocket 长连接）。
2. `pip install 'cursor-pulse[feishu]'`，设置 `BOT_PLATFORM=feishu` 与 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（可选 `FEISHU_GROUP_CHAT_ID`）。

### 2.2 配置

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

生产至少配置：

| 变量 | 说明 |
|------|------|
| `ADMIN_PASSWORD` | 本地超管密码（Web-only 必配） |
| `ADMIN_CHANNEL_USER_IDS` | 渠道管理员 userid（逗号分隔）；旧名 `DINGTALK_ADMIN_USER_IDS` 仍兼容 |
| `BOT_PLATFORM` | `none` / `dingtalk` / `feishu` |
| `DINGTALK_*` / `FEISHU_*` | 对应 IM 凭证（可选） |
| `JWT_SECRET` | Web JWT（生产必配） |
| `PULSE_CREDENTIAL_ENCRYPTION_KEY` | 凭证加密 |
| `PULSE_INTERNAL_SERVICE_TOKEN` | Pulse / Proxy 内部 API |
| `PULSE_INTERNAL_TOKEN` | Assistant→Pulse（通常与上项同值） |
| `PULSE_BASE_URL` | Assistant / Proxy 访问 Pulse 的 URL |
| `ASSISTANT_SERVICE_TOKEN` / `ASSISTANT_SECRET_KEY` | 启用 Assistant 时 |

更多变量见 `.env.example`。安全要求见 [../SECURITY.md](../SECURITY.md)。

### 2.3 Docker（推荐）

只在 `docker/` 目录操作，详见 [../docker/README.md](../docker/README.md)：

```bash
cd docker
./scripts/setup.sh
# 编辑 .env（至少 JWT / 加密密钥 / ADMIN_PASSWORD；IM 可选）
docker compose up -d --build                      # Web-only：init-db + web + channel
docker compose --profile full up -d --build       # 含 Assistant
```

可选 Proxy：`docker compose -f docker-compose.proxy.yml up -d --build`。  
Postgres：叠加 `docker-compose.postgres.yml`，并设置 `DATABASE_URL`（非默认）。

### 2.4 裸机

```bash
pip install -e ".[web]"          # 飞书另加 feishu；按需 pdf,postgres,s3
pulse init-db
pulse web --host 0.0.0.0 --port 8080
pulse channel                    # 另一进程（BOT_PLATFORM=none 时仅调度）
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

# 门户管理员（Web-only）
pulse admin bootstrap --user-id admin --name "管理员" --password <密码> --channel web
```

机器人命令说明：[bot-commands.md](bot-commands.md)。

## 2.5 身份字段迁移（钉钉-only → 中性通道）

`pulse init-db` / `migrate_schema` 会：

1. 将 `members.dingtalk_user_id` → `channel_user_id`，并写入 `channel='dingtalk'`
2. `manager_dingtalk_user_id` → `manager_channel_user_id`
3. `reminder_logs.dingtalk_msg_id` → `channel_msg_id`
4. 删除旧列（SQLite 可能重建表）

配置侧：

| 旧 | 新 |
|----|----|
| `admin.dingtalk_user_ids` | `admin.channel_user_ids` |
| `DINGTALK_ADMIN_USER_IDS` | `ADMIN_CHANNEL_USER_IDS`（旧 env 启动时仍读入并 warning） |

升级后请重新登录门户（JWT payload 字段已改名）。

**默认渠道变更：** 新默认 `BOT_PLATFORM=none`（仅 Web）。已有钉钉部署必须在 `.env` / `config.yaml` 中显式设置 `BOT_PLATFORM=dingtalk`，否则 channel 进程只跑调度、不再连钉钉 Stream。

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
# init-db 为 oneshot，compose up 会自动幂等迁移；裸机或排查时：
pulse init-db
# 仅当 init-db 容器失败需单独重跑：cd docker && docker compose run --rm init-db
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

## 9. 凭证加密密钥轮换

更换 `PULSE_CREDENTIAL_ENCRYPTION_KEY` 前必须重加密库内凭证，否则 Cursor API Key / 代理 key 无法解密。

1. **停写进程：** `pulse web`、`pulse channel`、Assistant、Go Proxy（避免同步或绑定写入半新半旧数据）。
2. **备份数据库：** SQLite 见 §5；Postgres 用 `pg_dump`。
3. **重加密（推荐 CLI）：**

```bash
export OLD_KEY="<当前 PULSE_CREDENTIAL_ENCRYPTION_KEY>"
export NEW_KEY="<新高熵密钥>"

# 先 dry-run 确认可解密条数
pulse rotate-credential-key --old "$OLD_KEY" --new "$NEW_KEY" --dry-run

# 确认 skipped=0 后执行
pulse rotate-credential-key --old "$OLD_KEY" --new "$NEW_KEY"
```

覆盖 `ai_account_credentials.encrypted_value`、`key_loans.alias_encrypted_key`、`proxy_keys.encrypted_key`。

4. **更新配置：** 将 `.env` / `docker/.env` 与 `config.yaml` 中的 `PULSE_CREDENTIAL_ENCRYPTION_KEY` 改为 `NEW_KEY`。
5. **重启** web / channel / assistant / proxy，抽查绑定 Key 与代理 authorize。

**无 CLI 时的 Python 片段（与 CLI 等价）：**

```python
from pulse.config import load_config
from pulse.ingestion.credentials import rotate_credential_encryption
from pulse.storage.db import init_db

cfg = load_config("config.yaml")
session = init_db(cfg.storage.database_url)()
stats = rotate_credential_encryption(session, old_key=OLD, new_key=NEW)
print(stats)
session.close()
```

`ASSISTANT_SECRET_KEY` 与 Pulse 凭证密钥无关；轮换 Assistant Secret Store 见 Assistant 文档。

## 8. CLI 速查

| 命令 | 作用 |
|------|------|
| `pulse init-db` | 建库 + 幂等 seed 厂家/套餐（`--no-seed` 可跳过） |
| `pulse init-v2 --seed` | 仅重新 seed 目录（兼容旧命令） |
| `pulse rotate-credential-key` | 轮换凭证加密密钥后重加密库内 blob |
| `pulse channel` | 渠道 + 调度 |
| `pulse web` | 控制面 HTTP |
| `pulse aggregate` / `report` / `export` | 聚合 / 月报 / 导出 |
| `pulse admin bootstrap\|grant\|revoke` | 门户账号 |
| `pulse reprice` / `reprice-proxy` | 计价重算（进阶） |
