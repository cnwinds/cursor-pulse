#!/usr/bin/env bash
# 从本地开发机迁移 data/ 到 Docker 部署目录
#
# 用法:
#   ./scripts/migrate-data.sh                          # 默认从仓库根 data/
#   ./scripts/migrate-data.sh /path/to/local/data
#   ./scripts/migrate-data.sh user@server:/opt/cursor-pulse/docker/data  # rsync 到远程
#
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DOCKER_DIR/.." && pwd)"
SRC="${1:-$REPO_ROOT/data}"
DEST="$DOCKER_DIR/data"

if [[ "$SRC" == *:* && "$SRC" == *@* ]]; then
  echo "同步到远程: $SRC"
  rsync -av --delete \
    --exclude '*.db-shm' --exclude '*.db-wal' \
    "$REPO_ROOT/data/" "$SRC/"
  echo "远程同步完成"
  exit 0
fi

if [[ ! -d "$SRC" ]]; then
  echo "源目录不存在: $SRC" >&2
  exit 1
fi

echo "源: $SRC"
echo "目标: $DEST"
mkdir -p "$DEST"

# 若源库处于 WAL 模式，优先使用 sqlite3 backup（更干净）
backup_db() {
  local name="$1"
  if command -v sqlite3 >/dev/null 2>&1 && [[ -f "$SRC/$name" ]]; then
    echo "  sqlite3 backup: $name"
    sqlite3 "$SRC/$name" ".backup '$DEST/$name'"
  elif [[ -f "$SRC/$name" ]]; then
    cp "$SRC/$name" "$DEST/$name"
  fi
}

backup_db pulse.db
backup_db assistant.db

# 原始文件
if [[ -d "$SRC/raw" ]]; then
  rsync -av "$SRC/raw/" "$DEST/raw/"
fi

echo ""
echo "迁移完成。请在服务器上执行:"
echo "  cd docker"
echo "  docker compose --profile tools run --rm init-db   # schema 迁移"
echo "  docker compose up -d"
