#!/usr/bin/env bash
# SQLite 热备份（服务运行中也可执行，建议先停容器）
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$DOCKER_DIR/data"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$DOCKER_DIR/backups/$STAMP"

mkdir -p "$BACKUP"

for db in pulse.db assistant.db; do
  if [[ -f "$DATA/$db" ]]; then
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$DATA/$db" ".backup '$BACKUP/$db'"
    else
      cp "$DATA/$db" "$BACKUP/$db"
    fi
    echo "已备份 $db -> $BACKUP/$db"
  fi
done

if [[ -d "$DATA/raw" ]]; then
  cp -a "$DATA/raw" "$BACKUP/raw"
  echo "已备份 raw/ -> $BACKUP/raw"
fi

echo "备份目录: $BACKUP"
