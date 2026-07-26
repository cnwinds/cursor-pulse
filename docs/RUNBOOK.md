# 运维手册

面向自托管部署的管理员。时区默认与 `config.yaml` → `collection.timezone` 一致（示例为 `Asia/Shanghai`）。

本项目只服务 **Cursor**：台账、API Key 同步、借 Key、可选 Proxy。不提供 CSV/截图采集或其他 AI 厂商台账。

## 1. 进程与数据

| 组件 | 命令 | 说明 |
|------|------|------|
| 渠道 + 调度 | `pulse channel` | Cursor 同步 tick + 借 Key 过期；`dingtalk`/`feishu` 时另含 IM 入站 |
| 管理 API | `pulse web` | JWT / 本地密码 / 可选 IM 扫码；默认 `:8080` |
| Vue（开发） | `web-admin` → `npm run dev` | `:5173`，代理 `/api` |
| Vue（生产） | `npm run build` 后由 web 挂载 `/admin/` | |
| 可选 Assistant | `python -m assistant_platform serve` | `:8090` |
| 可选 Proxy | `cursor-pulse start proxy` | `:8317` |
| 数据库 | `data/pulse.db` 或 Postgres | |

本地一键：`.\cursor-pulse.bat start` / `./cursor-pulse.sh start`。架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。代理分层见 [PROXY_LAYERS.md](PROXY_LAYERS.md)。

## 2. 部署

### 2.0 Web-only（推荐开源最小形态）

```bash
# .env
BOT_PLATFORM=none
ADMIN_PASSWORD=<强密码>
JWT_SECRET=<高熵，生产 ≥32 字节>
PULSE_CREDENTIAL_ENCRYPTION_KEY=<高熵>

pulse init-db   # 建表 + seed Cursor 套餐（台账下拉依赖此步）
pulse admin bootstrap --user-id admin --name "管理员" --password '<密码>' --channel web
pulse web
# 另开：pulse channel   # Cursor 同步 / 借 Key 过期
```

登录后在 **用户管理 → 创建用户** 添加本地账号，并在台账中选为负责人、绑定 API Key。  
成员可在 **我的借用** 自助申请临时 Key；管理员可在 **借用记录** 代分配。

`ADMIN_PASSWORD` 仅用于首次 bootstrap / 超管 `admin`；首次成功登录后写入库内 `password_hash`，之后以库为准。

### 2.0b 门户身份

| 路径 | 做法 |
|------|------|
| 纯 Web | 创建本地用户 → 挂台账 → 用户名密码登录 |
| 先本地后 IM | 创建用户并挂台账 → 配置钉钉/飞书 → **关联渠道** |
| 先 IM | 通讯录同步 / OAuth 待审批 → 审批开通 → 挂台账；可「设密码」补 Web 登录 |

### 2.1 钉钉（可选）

1. `pip install 'cursor-pulse[dingtalk]'`
2. 企业内部应用 + Stream 机器人；填写 `DINGTALK_*`；`BOT_PLATFORM=dingtalk`

### 2.1b 飞书（可选）

1. `pip install 'cursor-pulse[feishu]'`
2. 自建应用 + WebSocket；填写 `FEISHU_*`；`BOT_PLATFORM=feishu`

### 2.2 配置

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

| 变量 | 说明 |
|------|------|
| `ADMIN_PASSWORD` | 本地超管密码（Web-only 必配） |
| `ADMIN_CHANNEL_USER_IDS` | 渠道管理员 userid（可选） |
| `BOT_PLATFORM` | `none` / `dingtalk` / `feishu` |
| `JWT_SECRET` | Web JWT（生产必配） |
| `PULSE_CREDENTIAL_ENCRYPTION_KEY` | 凭证加密 |
| `PULSE_INTERNAL_SERVICE_TOKEN` | Pulse / Proxy 内部 API |
| `PULSE_INTERNAL_TOKEN` | Assistant→Pulse（通常与上项同值） |
| `PULSE_BASE_URL` | Assistant / Proxy 访问 Pulse 的 URL |

更多见 `.env.example` 与 [../SECURITY.md](../SECURITY.md)。

### 2.3 Docker

```bash
cd docker
./scripts/setup.sh
docker compose up -d --build                      # init-db + web + channel
docker compose --profile full up -d --build       # 含 Assistant
```

可选 Proxy：`docker compose -f docker-compose.proxy.yml up -d --build`。详见 [../docker/README.md](../docker/README.md)。

### 2.4 裸机

```bash
pip install -e ".[web]"          # 飞书另加 feishu；钉钉另加 dingtalk
pulse init-db
pulse web --host 0.0.0.0 --port 8080
pulse channel
```

## 3. 日常操作

```bash
# 门户管理员
pulse admin bootstrap --user-id admin --name "管理员" --password <密码> --channel web

# 凭证密钥轮换（见 §7）
pulse rotate-credential-key --old "$OLD" --new "$NEW" --dry-run
```

可选 IM 命令：[bot-commands.md](bot-commands.md)。

## 4. 身份字段迁移（钉钉-only → 中性通道）

`pulse init-db` / `migrate_schema` 会硬切迁移旧列。配置侧：

| 旧 | 新 |
|----|----|
| `admin.dingtalk_user_ids` | `admin.channel_user_ids` |
| `DINGTALK_ADMIN_USER_IDS` | `ADMIN_CHANNEL_USER_IDS` |

升级后请重新登录门户。新默认 `BOT_PLATFORM=none`；已有钉钉部署须显式 `BOT_PLATFORM=dingtalk`。

## 5. 健康检查

- HTTP：`GET /health`（web / assistant）
- 冒烟：管理后台登录、绑定 Key、额度看板

## 6. 备份与恢复

备份：`data/`（或 Postgres dump）、`config.yaml`、加密密钥材料（勿入库）。

```bash
sqlite3 data/pulse.db ".backup data/pulse-backup.db"
```

## 7. 凭证加密密钥轮换

1. 停写：`pulse web` / `pulse channel` / Assistant / Proxy  
2. 备份数据库  
3. `pulse rotate-credential-key --old … --new …`（先 `--dry-run`）  
4. 更新 `.env` 中的 `PULSE_CREDENTIAL_ENCRYPTION_KEY`  
5. 重启并抽查绑定 Key / 代理 authorize  

## 8. 升级

```bash
git pull
pip install -e ".[web]"
pulse init-db
# Docker: cd docker && docker compose build && docker compose up -d
```

## 9. 故障速查

| 现象 | 排查 |
|------|------|
| 机器人无响应 | Stream；AppKey/Secret；是否在群 |
| Web 401 | `JWT_SECRET`、角色、OAuth 回调 |
| Internal 503 | 未配置 `PULSE_INTERNAL_SERVICE_TOKEN` |
| 同步无数据 | 是否已绑定 Key；`pulse channel` 是否在跑；加密密钥是否匹配 |

## 10. CLI 速查

| 命令 | 作用 |
|------|------|
| `pulse init-db` | 建库 + seed Cursor 目录（`--no-seed` 可跳过） |
| `pulse init-v2 --seed` | 仅重新 seed（兼容旧命令） |
| `pulse rotate-credential-key` | 轮换凭证加密密钥 |
| `pulse channel` | IM（可选）+ Cursor 同步 / 借 Key 过期 |
| `pulse web` | 控制面 HTTP |
| `pulse admin bootstrap\|grant\|revoke` | 门户账号 |
| `pulse reprice` / `reprice-proxy` | 计价重算（进阶） |
