# syntax=docker/dockerfile:1

# ── Stage 1: Vue admin SPA ──────────────────────────────────────────────────
FROM node:20-alpine AS admin-build

WORKDIR /app/web-admin

COPY web-admin/package.json web-admin/package-lock.json ./
RUN npm ci

COPY web-admin/ ./
RUN npm run build


# ── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PULSE_CONFIG=/app/config/config.yaml

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY pulse ./pulse
COPY assistant_platform ./assistant_platform

RUN pip install --upgrade pip \
    && pip install ".[web,pdf,postgres,s3]"

COPY --from=admin-build /app/web-admin/dist ./web-admin/dist

RUN mkdir -p data/raw data/inbox

VOLUME ["/app/data"]

# Default CMD is overridden per service in docker-compose.yml
CMD ["pulse", "channel", "-c", "/app/config/config.yaml"]
