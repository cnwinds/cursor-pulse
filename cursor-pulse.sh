#!/usr/bin/env bash
# Local / mode-1 control plane: prepare workspace then start all services.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

pulse_bin() {
  if [[ -x "$ROOT/.venv/bin/pulse" ]]; then
    echo "$ROOT/.venv/bin/pulse"
  elif command -v pulse >/dev/null 2>&1; then
    command -v pulse
  else
    return 1
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
    echo "未找到 python，请先安装 Python 3.11+" >&2
    exit 1
  fi
}

ensure_venv() {
  if [[ -x "$ROOT/.venv/bin/pulse" ]]; then
    return 0
  fi
  echo "[setup] 创建 .venv 并安装依赖 (. [dev,web])…"
  local py
  py="$(python_bin)"
  "$py" -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -U pip
  "$ROOT/.venv/bin/pip" install -e ".[dev,web]"
}

prepare_workspace() {
  ensure_venv
  local keep_docker=0
  local arg
  for arg in "$@"; do
    if [[ "$arg" == "--keep-docker" ]]; then
      keep_docker=1
    fi
  done
  if (( keep_docker )); then
    "$(python_bin)" -m pulse.dev.prepare --keep-docker
  else
    "$(python_bin)" -m pulse.dev.prepare
  fi
}

usage() {
  cat <<'EOF'
Cursor Pulse 本地开发（模式 1）

用法:
  ./cursor-pulse.sh start [服务…] [--keep-docker]   准备环境并启动（默认全部）
  ./cursor-pulse.sh stop  [服务…]                   停止服务
  ./cursor-pulse.sh restart [服务…] [--keep-docker] 重启
  ./cursor-pulse.sh prepare [--keep-docker]         只准备 data/.env/代理，不启动
  ./cursor-pulse.sh log <服务> [-f] [-n N]           查看日志
  ./cursor-pulse.sh status                          运行状态

服务: web | admin | channel | assistant | proxy
  默认 start = web + assistant + channel + admin + proxy
  channel 若缺少 DINGTALK_APP_KEY/SECRET 会自动跳过并提示

准备内容:
  - data/ → docker/data（共用生产库）
  - .env 从 docker/.env 合并，并改写本机 URL / CORS
  - web 监听 0.0.0.0:8080；Vite 0.0.0.0:5173
  - 构建 Go proxy（如有 go）
  - 停掉占用端口的 docker web/assistant/channel/proxy（可用 --keep-docker 跳过）

访问:
  管理后台（热更新）  http://127.0.0.1:5173  或  http://<局域网IP>:5173
  API                 http://127.0.0.1:8080
  Assistant           http://127.0.0.1:8090
  Proxy               http://127.0.0.1:8317

日志目录: .dev/logs/
EOF
}

filter_keep_docker() {
  local -a out=()
  local a
  for a in "$@"; do
    [[ "$a" == "--keep-docker" ]] && continue
    out+=("$a")
  done
  if ((${#out[@]})); then
    printf '%s\n' "${out[@]}"
  fi
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  prepare|setup)
    prepare_workspace "$@"
    ;;
  start)
    prepare_workspace "$@"
    mapfile -t services < <(filter_keep_docker "$@")
    PULSE="$(pulse_bin)"
    if ((${#services[@]})); then
      exec "$PULSE" dev start "${services[@]}"
    fi
    exec "$PULSE" dev start
    ;;
  restart)
    prepare_workspace "$@"
    mapfile -t services < <(filter_keep_docker "$@")
    PULSE="$(pulse_bin)"
    if ((${#services[@]})); then
      exec "$PULSE" dev restart "${services[@]}"
    fi
    exec "$PULSE" dev restart
    ;;
  stop)
    ensure_venv
    PULSE="$(pulse_bin)"
    if (($#)); then
      exec "$PULSE" dev stop "$@"
    fi
    exec "$PULSE" dev stop
    ;;
  log|logs)
    ensure_venv
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
    ensure_venv
    exec "$(pulse_bin)" dev status
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
