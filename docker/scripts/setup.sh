#!/usr/bin/env bash
# 首次部署：准备 config.yaml 与 .env（生成高熵服务令牌，拒绝保留 change-me）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$DOCKER_DIR"

_rand() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python -c 'import secrets; print(secrets.token_hex(32))'
  fi
}

_upsert_env() {
  local key="$1" val="$2" file="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    local cur
    cur="$(grep "^${key}=" "$file" | head -n1 | cut -d= -f2-)"
    if [[ -z "$cur" || "$cur" == change-me* ]]; then
      # portable-ish in-place replace
      python - "$file" "$key" "$val" <<'PY'
import sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().splitlines()
out = []
found = False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={val}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={val}")
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
      echo "已写入/刷新 ${key}（随机值）"
    else
      echo "${key} 已存在且非占位，跳过"
    fi
  else
    echo "${key}=${val}" >>"$file"
    echo "已追加 ${key}"
  fi
}

if [[ ! -f config.yaml ]]; then
  cp "$ROOT/config.example.yaml" config.yaml
  echo "已创建 docker/config.yaml（来自 config.example.yaml）"
else
  echo "config.yaml 已存在，跳过"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已创建 docker/.env（来自 .env.example）"
else
  echo ".env 已存在"
fi

# 清空示例里的 change-me，并生成服务令牌
TOKEN_A="$(_rand)"
TOKEN_P="$(_rand)"
_upsert_env ASSISTANT_SERVICE_TOKEN "$TOKEN_A" .env
_upsert_env PULSE_INTERNAL_SERVICE_TOKEN "$TOKEN_P" .env
_upsert_env PULSE_INTERNAL_TOKEN "$TOKEN_P" .env

# 若 JWT / 加密密钥为空则生成
_upsert_env JWT_SECRET "$(_rand)" .env
_upsert_env ASSISTANT_SECRET_KEY "$(_rand)" .env
_upsert_env PULSE_CREDENTIAL_ENCRYPTION_KEY "$(_rand)" .env

mkdir -p data/raw/inbox
echo "目录 data/ 已就绪"

echo ""
echo "下一步："
echo "  1. 编辑 docker/.env：填入钉钉凭证；确认 JWT / 加密密钥已生成"
echo "  2. 若有本地数据，执行: ./scripts/migrate-data.sh <本地 data 目录>"
echo "  3. docker compose up -d --build"
echo "     （init-db 会在 up 时自动执行；库与配置在 docker/data、docker/config.yaml）"
echo ""
echo "注意：生产环境禁止使用 change-me-* 占位令牌；应用启动时会拒绝。"
