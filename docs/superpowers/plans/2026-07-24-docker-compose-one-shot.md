# Docker Compose 一键编排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `docker compose up -d` 自动完成 Pulse schema 初始化并拉起 web/assistant/channel，库与配置 bind mount 到宿主机；Proxy 独立 compose。

**Architecture:** 将 `init-db` 从 `profiles: [tools]` 提升为默认 oneshot 服务，业务容器 `depends_on` + `service_completed_successfully`；保留现有 data/config 挂载；新增独立 proxy 编排文件。

**Tech Stack:** Docker Compose v2、现有 `docker/Dockerfile`、SQLite bind mount

---

## File map

| 文件 | 职责 |
|------|------|
| `docker/docker-compose.yml` | 主栈：init-db → web → assistant/channel |
| `docker/docker-compose.proxy.yml` | 可选 Proxy 独立编排 |
| `docker/Dockerfile.proxy` | Go proxy 镜像 |
| `docker/scripts/setup.sh` | 首次准备 + 下一步提示 |
| `docker/README.md` | 部署说明 |
| `docs/RUNBOOK.md` / `README.md` | 对齐一键路径 |
| `docs/superpowers/specs/2026-07-24-docker-compose-one-shot-design.md` | 已写设计 |

---

### Task 1: 主 compose — 自动 init-db

**Files:**
- Modify: `docker/docker-compose.yml`

- [ ] **Step 1: 调整 `init-db` 与依赖**

将 `init-db` 去掉 `profiles: [tools]`，保持 `restart: "no"`。给 `web` / `assistant` / `channel` 增加对 `init-db` 的 `depends_on`（`condition: service_completed_successfully`）。`assistant`/`channel` 保留对 `web` 的 healthy 依赖。`init-v2` 仍留在 `profiles: [tools]`。

目标片段：

```yaml
  init-db:
    <<: *app-common
    container_name: cursor-pulse-init-db
    restart: "no"
    command: ["pulse", "init-db", "-c", "/app/config/config.yaml"]

  web:
    <<: *app-common
    container_name: cursor-pulse-web
    depends_on:
      init-db:
        condition: service_completed_successfully
    # ... existing command/ports/healthcheck ...

  assistant:
    <<: *app-common
    depends_on:
      init-db:
        condition: service_completed_successfully
      web:
        condition: service_healthy
    # ...

  channel:
    <<: *app-common
    depends_on:
      init-db:
        condition: service_completed_successfully
      web:
        condition: service_healthy
      assistant:
        condition: service_healthy
```

确认 volumes 仍为：

```yaml
  volumes:
    - ./data:/app/data
    - ./config.yaml:/app/config/config.yaml:ro
```

- [ ] **Step 2: 静态检查 compose**

Run: `docker compose -f docker/docker-compose.yml config`
Expected: 无报错；输出中 `init-db` 无 profiles；`web.depends_on.init-db` 存在。

- [ ] **Step 3: Commit**

```bash
git add docker/docker-compose.yml
git commit -m "feat(docker): auto-run init-db on compose up"
```

---

### Task 2: Proxy 独立编排

**Files:**
- Create: `docker/Dockerfile.proxy`
- Create: `docker/docker-compose.proxy.yml`

- [ ] **Step 1: 写 Go 多阶段 Dockerfile**

`docker/Dockerfile.proxy`：

```dockerfile
# syntax=docker/dockerfile:1
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY proxy/go.mod ./
COPY proxy/*.go ./
RUN CGO_ENABLED=0 go build -o /out/cursor-pulse-proxy .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=build /out/cursor-pulse-proxy /usr/local/bin/cursor-pulse-proxy
RUN mkdir -p /data
VOLUME ["/data"]
ENV HOME=/data
EXPOSE 8317
ENTRYPOINT ["cursor-pulse-proxy"]
CMD ["-listen", "0.0.0.0:8317"]
```

（若 `go.mod` 声明更高版本，对齐 go 镜像小版本。）

- [ ] **Step 2: 写独立 compose**

`docker/docker-compose.proxy.yml`：

```yaml
name: cursor-pulse-proxy

services:
  proxy:
    image: cursor-pulse-proxy:latest
    build:
      context: ..
      dockerfile: docker/Dockerfile.proxy
    container_name: cursor-pulse-proxy
    env_file:
      - .env
    environment:
      PULSE_BASE_URL: ${PULSE_BASE_URL:-http://host.docker.internal:8080}
      PULSE_INTERNAL_SERVICE_TOKEN: ${PULSE_INTERNAL_SERVICE_TOKEN}
    ports:
      - "${PULSE_PROXY_PORT:-8317}:8317"
    volumes:
      - ./proxy-data:/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
```

说明：独立网络时默认经 `host.docker.internal` 访问宿主机已映射的 web:8080；若与主 compose 同项目网络联调，文档写明可改 `PULSE_BASE_URL=http://web:8080` 并 `docker compose -f docker-compose.yml -f docker-compose.proxy.yml`（高级，非默认）。

默认推荐：主栈先起，proxy 单独 `docker compose -f docker-compose.proxy.yml up -d`，`PULSE_BASE_URL=http://host.docker.internal:${PULSE_WEB_PORT}`。

- [ ] **Step 3: Commit**

```bash
git add docker/Dockerfile.proxy docker/docker-compose.proxy.yml
git commit -m "feat(docker): add standalone proxy compose"
```

---

### Task 3: 文档与 setup 提示对齐

**Files:**
- Modify: `docker/scripts/setup.sh`
- Modify: `docker/README.md`
- Modify: `docs/RUNBOOK.md`
- Modify: `README.md`
- Modify: `proxy/README.md`（部署一节加 Docker 链接）
- Modify: `docker/.env.example`（可选 `PULSE_PROXY_PORT` 注释）

- [ ] **Step 1: 改 setup.sh 下一步**

末尾改为：

```
echo "下一步："
echo "  1. 编辑 docker/.env：填入钉钉凭证；确认 JWT / 加密密钥已生成"
echo "  2. 若有本地数据，执行: ./scripts/migrate-data.sh <本地 data 目录>"
echo "  3. docker compose up -d --build"
echo "  （init-db 会在 up 时自动执行）"
```

- [ ] **Step 2: 改 docker/README.md**

- 启动节改为单条 `docker compose up -d --build`
- 强调 `data/`、`config.yaml`、`.env` 映射
- 删除「常规路径必须 profile run init-db」；保留「仅排查时可手动 run init-db」
- 增加 Proxy 独立 compose 小节
- 表格仍列三业务容器 + init-db oneshot

- [ ] **Step 3: 对齐 RUNBOOK / 根 README / proxy README**

同样把 Docker 步骤收成 setup → `up -d --build`。

- [ ] **Step 4: Commit**

```bash
git add docker/scripts/setup.sh docker/README.md docs/RUNBOOK.md README.md proxy/README.md docker/.env.example
git commit -m "docs(docker): one-shot compose up path"
```

---

### Task 4: 验证

- [ ] **Step 1: `docker compose config` 通过**
- [ ] **Step 2: 若本机有 Docker，在 `docker/` 对已有 `.env`/`config.yaml` 执行 `docker compose up -d --build`，确认 `init-db` exited 0 且 `/health` 200；确认 `docker/data/*.db` 在宿主机可见**
- [ ] **Step 3: 确认 `docker compose -f docker-compose.proxy.yml config` 通过（可不实际起 proxy）**

若无 Docker 环境，至少完成 Step 1/3 的 `config` 校验并在完成说明中注明未做实跑。

---

## Spec coverage

| Spec 项 | Task |
|---------|------|
| 主 compose 三件套 + auto init-db | 1 |
| bind mount data/config | 1（保持） |
| env 开关文档 | 3 |
| Proxy 独立 | 2 |
| 文档一键路径 | 3 |
| 验收 | 4 |
