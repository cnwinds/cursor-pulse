#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

pulse_bin() {
  if [[ -x "$ROOT/.venv/bin/pulse" ]]; then
    echo "$ROOT/.venv/bin/pulse"
  elif command -v pulse >/dev/null 2>&1; then
    command -v pulse
  else
    echo "未找到 pulse，请先执行: python -m venv .venv && .venv/bin/pip install -e '.[dev,web]'" >&2
    exit 1
  fi
}

python_bin() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    echo "未找到 python，请先执行: python -m venv .venv && .venv/bin/pip install -e '.[dev,web]'" >&2
    exit 1
  fi
}

usage() {
  cat <<'EOF'
Cursor Pulse 开发环境管理

用法:
  ./cursor-pulse.sh start [web] [admin] [channel] [assistant] [proxy]   启动服务（默认全部不含 proxy）
  ./cursor-pulse.sh stop  [web] [admin] [channel] [assistant] [proxy]   停止服务
  ./cursor-pulse.sh restart [web] [admin] [channel] [assistant] [proxy] 重启服务（默认全部不含 proxy）
  ./cursor-pulse.sh log <web|admin|channel|assistant|proxy> [-f] [-n N]  查看日志
  ./cursor-pulse.sh status                      查看运行状态

服务:
  web        管理后台 API          http://127.0.0.1:8080
  admin      Vue 开发前端          http://127.0.0.1:5173
  channel    渠道适配 + 调度
  assistant  Assistant Platform    http://127.0.0.1:8090
  proxy      Cursor 代理（Go）     http://127.0.0.1:8317  （需 Go；不在默认 start）

代理建议：先 start 起 web，再 ./cursor-pulse.sh start proxy。

日志目录: .dev/logs/
EOF
}

PULSE="$(pulse_bin)"
cmd="${1:-help}"
shift || true

case "$cmd" in
  start|stop|restart)
    if (($#)); then
      exec "$PULSE" dev "$cmd" "$@"
    fi
    exec "$PULSE" dev "$cmd"
    ;;
  log|logs)
    service="web"
    args=()
    for arg in "$@"; do
      case "$arg" in
        web|admin|channel|assistant|proxy) service="$arg" ;;
        *) args+=("$arg") ;;
      esac
    done
    exec "$(python_bin)" -m pulse.dev logs "$service" "${args[@]}"
    ;;
  status)
    exec "$PULSE" dev status
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "未知命令: $cmd" >&2
    usage
    exit 1
    ;;
esac
