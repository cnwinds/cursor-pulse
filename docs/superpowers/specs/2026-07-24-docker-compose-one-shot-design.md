# Docker Compose 一键编排设计

日期：2026-07-24  
状态：已确认（对话拍板后开干）

## 背景

仓库已有 `docker/` 生产编排（同一镜像跑 `web` / `assistant` / `channel`），但首次部署需多步：`setup.sh` → `build` → `profile tools` 跑 `init-db` → `up`。  
目标改为：**一个主 compose 编排核心三件套**，`docker compose up -d` 时自动完成新环境 schema 初始化；可选能力用环境变量控制；库与配置 bind mount 到宿主机文件。

## 目标

1. 主文件 `docker/docker-compose.yml` 编排：`init-db` + `web` + `assistant` + `channel`。
2. `docker compose up -d`（可加 `--build`）自动跑幂等 `pulse init-db`，无需再 `--profile tools run init-db`。
3. 数据库与配置映射到宿主机本地路径（bind mount）。
4. 功能可选部分通过 `.env` 控制，不靠拆多个主 compose。
5. Proxy 使用**独立** compose，不进入主文件。
6. 默认 SQLite；Postgres 仍仅作可选 overlay 文档，不进主 compose。
7. 不编 Nginx；对外只暴露 `PULSE_WEB_PORT`（默认 8080）。

## 非目标

- 单容器内用 supervisord 跑多进程。
- `up` 时自动生成并持久化密钥（仍用 `scripts/setup.sh` + 人工填钉钉）。
- 用 env 停掉 `assistant` / `channel` 容器。
- 主 compose 内嵌 Postgres / Nginx / Let's Encrypt。
- 把根目录旧版单容器 compose（若仍存在）作为生产路径。

## 架构

```
宿主机 docker/
├── .env                 → env_file 注入各容器
├── config.yaml          → :ro → /app/config/config.yaml
└── data/                → /app/data  （pulse.db / assistant.db / raw/）

compose 启动顺序:
  init-db (oneshot, 成功退出)
       ↓ service_completed_successfully
      web (healthy :8080)
       ↓
  assistant + channel
```

- 同一镜像 `cursor-pulse:latest`（`docker/Dockerfile` 多阶段：Vue build + Python runtime）。
- `init-db`：`restart: "no"`，命令 `pulse init-db -c /app/config/config.yaml`。
- `web` / `assistant` / `channel`：`depends_on` 等待 `init-db` 完成；`assistant`/`channel` 另等 `web` healthy（与现网一致）。
- `init-v2` 保留在 `profiles: [tools]`，不自动跑。

## 宿主机映射

| 宿主机 | 容器 | 说明 |
|--------|------|------|
| `docker/data/` | `/app/data` | SQLite 与附件；勿用匿名卷替代 |
| `docker/config.yaml` | `/app/config/config.yaml:ro` | 业务配置 |
| `docker/.env` | `env_file` | 密钥与开关（不挂进容器 FS） |

备份：停写或 `sqlite3 .backup` 后拷贝 `docker/data/`。迁移本地库仍用 `scripts/migrate-data.sh`。

## 初始化流程

**首次（人工一次）**

1. `cd docker && ./scripts/setup.sh` — 生成 `config.yaml`、`.env`、随机令牌/密钥，建 `data/`。
2. 编辑 `.env`：钉钉凭证、生产域名 CORS/OAuth。
3. `docker compose up -d --build`。

**每次 `up` 自动**

1. `init-db` 幂等迁移 Pulse schema 后退出。
2. 起 `web` → healthy。
3. 起 `assistant`、`channel`（assistant 进程启动时仍会补齐自身 DB schema）。

密钥为空或 `change-me-*` 时，现有启动校验继续拒绝；不在 compose 内自动改写 `.env`。

## 环境变量开关

| 类别 | 变量 | 说明 |
|------|------|------|
| 必填 | `DINGTALK_APP_KEY/SECRET/ROBOT_CODE`、`DINGTALK_ADMIN_USER_IDS` | 钉钉 |
| 必填 | `JWT_SECRET`、服务/加密令牌 | `setup.sh` 可生成 |
| 端口 | `PULSE_WEB_PORT` | 默认 8080 |
| 功能 | `ASSISTANT_LLM_*`、`WEB_SEARCH_ENABLED`/`TAVILY_API_KEY` | 可选能力 |
| 功能 | `ASSISTANT_MIRROR_ENABLED` | 默认 true；不删容器 |
| 域名 | `DINGTALK_OAUTH_REDIRECT_URI`、`WEB_CORS_ORIGINS` | 生产 |

容器间：`PULSE_BASE_URL=http://web:8080`、`ASSISTANT_MIRROR_BASE_URL=http://assistant:8090`（compose 已定，一般勿改）。

## Proxy（独立）

- 新增独立编排（建议 `docker/docker-compose.proxy.yml` + Go 镜像），需主栈已起且 `.env` 中 `PULSE_BASE_URL` / `PULSE_INTERNAL_SERVICE_TOKEN` 可用。
- 主 README 仅链接「可选 Proxy」；不 `include` 进主 compose。
- CA / 数据目录同样 bind mount 到宿主机路径。

## 错误处理与运维

- `init-db` 失败 → 依赖服务不启动；`docker compose logs init-db` 排查。
- 缺 `.env` / `config.yaml` → `setup.sh` 或明确报错；文档要求先 setup。
- 单实例 `channel`：同一钉钉机器人勿多副本抢 Stream。
- 升级：`docker compose build && docker compose up -d`（自动再跑 init-db）。

## 验收标准

1. 干净目录：setup → 填钉钉 → `docker compose up -d --build` 后，`web`/`assistant`/`channel` 为 running，`init-db` 为 exited 0。
2. `curl http://127.0.0.1:${PULSE_WEB_PORT}/health` 成功。
3. `docker/data/pulse.db`（及 assistant 库）出现在宿主机 `docker/data/`。
4. 二次 `up -d` 仍成功（init-db 幂等）。
5. 文档不再要求手动 `--profile tools run init-db` 作为常规路径。
6. Proxy 有独立 compose 说明；主 `up` 不启动 proxy。

## 文档与脚本改动面

- `docker/docker-compose.yml` — init-db 默认依赖链
- `docker/scripts/setup.sh` — 下一步提示改为 `up -d --build`
- `docker/README.md`、`docs/RUNBOOK.md`、根 `README.md` Docker 小节 — 对齐一键路径
- Proxy：独立 compose + 简短 README 段落
- 保留 `docker-compose.postgres.yml` 为高级可选，标注非默认
