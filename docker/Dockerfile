# ── Stage 1: Vue admin SPA ──────────────────────────────────────────────────
# Base images via DaoCloud mirror (Docker Hub proxy)
ARG NODE_IMAGE=docker.m.daocloud.io/library/node:20-alpine
ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.11-slim

FROM ${NODE_IMAGE} AS admin-build

WORKDIR /app/web-admin

# npm 国内镜像
RUN npm config set registry https://registry.npmmirror.com

COPY web-admin/package.json web-admin/package-lock.json ./
RUN npm ci

COPY web-admin/ ./
# outDir is ../pulse/web/static (see web-admin/vite.config.ts)
RUN mkdir -p /app/pulse/web && npm run build


# ── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PULSE_CONFIG=/app/config/config.yaml \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

# Debian apt 国内镜像（python slim 基于 Debian）
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY pulse ./pulse
COPY assistant_platform ./assistant_platform

# Bake SPA into the package *before* pip install so site-packages includes it.
COPY --from=admin-build /app/pulse/web/static ./pulse/web/static

RUN pip install --upgrade pip \
    && pip install ".[web,pdf,postgres,s3]"

RUN mkdir -p data/raw data/inbox

VOLUME ["/app/data"]

# Default CMD is overridden per service in docker-compose.yml
CMD ["pulse", "channel", "-c", "/app/config/config.yaml"]
