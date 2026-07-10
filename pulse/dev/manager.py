from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pulse.dev.services import (
    DEFAULT_SERVICES,
    SERVICES,
    build_command,
    dev_dir,
    logs_dir,
    pids_dir,
    project_root,
)


class DevManagerError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_file(service: str) -> Path:
    return pids_dir() / f"{service}.json"


def _log_file(service: str) -> Path:
    return logs_dir() / f"{service}.log"


def _ensure_dirs() -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
    pids_dir().mkdir(parents=True, exist_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def _load_state(service: str) -> dict[str, Any] | None:
    path = _pid_file(service)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_state(service: str, state: dict[str, Any]) -> None:
    _ensure_dirs()
    _pid_file(service).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_state(service: str) -> None:
    path = _pid_file(service)
    if path.exists():
        path.unlink()


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        if sock.connect_ex((host, port)) == 0:
            return True
    if host == "127.0.0.1":
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex(("::1", port)) == 0
    return False


def _find_listening_pid(port: int) -> int | None:
    if sys.platform == "win32":
        result = subprocess.run(
            ["netstat", "-ano"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if "LISTENING" not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                local_port = int(parts[1].rsplit(":", 1)[-1])
                pid = int(parts[-1])
            except ValueError:
                continue
            if local_port == port and pid > 0:
                return pid
        return None

    import shutil

    lsof = shutil.which("lsof")
    if lsof:
        result = subprocess.run(
            [lsof, "-ti", f":{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])

    ss = shutil.which("ss")
    if ss:
        result = subprocess.run(
            [ss, "-ltnp", f"sport = :{port}"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            match = re.search(r"pid=(\d+)", line)
            if match:
                return int(match.group(1))
    return None


def _is_project_serve_command(command_line: str) -> bool:
    normalized = command_line.lower()
    root = str(project_root()).lower()
    if root not in normalized:
        return False
    return bool(re.search(r"(?:^|[\s\"'])serve(?:\s|$)", normalized))


def _iter_process_rows() -> list[tuple[int, str]]:
    if sys.platform == "win32":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'pulse|python' } | "
                "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        rows: list[tuple[int, str]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                rows.append((int(parts[0]), parts[1]))
            except ValueError:
                continue
        return rows

    result = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        check=False,
        capture_output=True,
        text=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return rows


def _find_bot_serve_pids(*, exclude: set[int] | None = None) -> list[int]:
    blocked = exclude or set()
    pids: list[int] = []
    for pid, command_line in _iter_process_rows():
        if pid in blocked or pid <= 0:
            continue
        if _is_project_serve_command(command_line):
            pids.append(pid)
    return pids


def _kill_pid(pid: int) -> None:
    if not _pid_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    try:
        os.killpg(os.getpgid(pid), 15)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _resolve_services(names: list[str] | None) -> list[str]:
    if not names:
        return list(DEFAULT_SERVICES)
    unknown = [name for name in names if name not in SERVICES]
    if unknown:
        raise DevManagerError(f"未知服务: {', '.join(unknown)}；可选: {', '.join(SERVICES)}")
    return names


def _resolve_existing_services(names: list[str] | None) -> list[str]:
    if names:
        return _resolve_services(names)
    running = [name for name in SERVICES if is_running(name)]
    port_bound = [
        name
        for name, meta in SERVICES.items()
        if meta.port is not None and _port_open(meta.port)
    ]
    targets = list(dict.fromkeys([*running, *port_bound]))
    if targets:
        return targets
    return list(DEFAULT_SERVICES)


def is_running(service: str) -> bool:
    state = _load_state(service)
    if state and _pid_alive(int(state["pid"])):
        return True
    if state:
        _clear_state(service)
    if service == "bot" and _find_bot_serve_pids():
        return True
    return False


def status() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, meta in SERVICES.items():
        state = _load_state(name)
        pid = int(state["pid"]) if state and state.get("pid") else None
        alive = pid is not None and _pid_alive(pid)
        if state and not alive:
            _clear_state(name)
            pid = None
        if name == "bot" and not alive:
            orphans = _find_bot_serve_pids()
            if orphans:
                alive = True
                pid = orphans[0]
        port_ok = _port_open(meta.port) if meta.port else None
        rows.append(
            {
                "service": name,
                "label": meta.label,
                "running": alive,
                "pid": pid if alive else None,
                "port": meta.port,
                "port_open": port_ok,
                "url": meta.url,
                "log_file": str(_log_file(name)),
                "started_at": state.get("started_at") if alive and state else None,
            }
        )
    return rows


def start(services: list[str] | None = None, *, config_path: str = "config.yaml") -> list[str]:
    _ensure_dirs()
    started: list[str] = []
    for name in _resolve_services(services):
        if is_running(name):
            print(f"[dev] {name} 已在运行 (pid={_load_state(name)['pid']})")
            continue

        command, cwd, extra = build_command(name, config_path=config_path)
        log_path = _log_file(name)
        log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
        log_handle.write(f"\n===== start {_utc_now()} =====\n")
        log_handle.flush()

        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        popen_kwargs.update(extra)

        try:
            proc = subprocess.Popen(command, **popen_kwargs)
        except FileNotFoundError as exc:
            log_handle.close()
            raise DevManagerError(f"启动 {name} 失败: {exc}") from exc

        _save_state(
            name,
            {
                "pid": proc.pid,
                "command": command,
                "cwd": str(cwd),
                "log_file": str(log_path),
                "started_at": _utc_now(),
            },
        )
        meta = SERVICES[name]
        url = meta.url or "(无固定端口)"
        print(f"[dev] 已启动 {name} ({meta.label}) pid={proc.pid} → {url}")
        print(f"[dev] 日志: {log_path}")
        started.append(name)

    if started:
        _wait_for_ports(started)
    return started


def _wait_for_ports(services: list[str], timeout: float = 20.0) -> None:
    pending = {
        name: SERVICES[name].port
        for name in services
        if SERVICES[name].port is not None
    }
    if not pending:
        return

    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        ready: list[str] = []
        for name, port in pending.items():
            if _port_open(port):
                ready.append(name)
        for name in ready:
            pending.pop(name)
            print(f"[dev] {name} 端口 {SERVICES[name].port} 已就绪")
        if pending:
            time.sleep(0.4)

    for name, port in pending.items():
        print(f"[dev] 警告: {name} 在 {timeout:.0f}s 内未监听端口 {port}，请查看 logs {name}")


def stop(services: list[str] | None = None) -> list[str]:
    stopped: list[str] = []
    for name in _resolve_existing_services(services):
        meta = SERVICES[name]
        state = _load_state(name)
        tracked_pid = int(state["pid"]) if state and state.get("pid") else None
        killed: list[str] = []

        if tracked_pid:
            _kill_pid(tracked_pid)
            _clear_state(name)
            killed.append(f"pid={tracked_pid}")
            time.sleep(0.3)

        if meta.port and _port_open(meta.port):
            port_pid = _find_listening_pid(meta.port)
            if port_pid and port_pid != tracked_pid:
                _kill_pid(port_pid)
                killed.append(f"端口进程 pid={port_pid}")
                time.sleep(0.2)

        if name == "bot":
            blocked = {tracked_pid} if tracked_pid else set()
            for orphan_pid in _find_bot_serve_pids(exclude=blocked):
                _kill_pid(orphan_pid)
                killed.append(f"serve 进程 pid={orphan_pid}")
                time.sleep(0.2)

        if killed:
            print(f"[dev] 已停止 {name} ({', '.join(killed)})")
            stopped.append(name)
        else:
            print(f"[dev] {name} 未运行")
    return stopped


def restart(services: list[str] | None = None, *, config_path: str = "config.yaml") -> list[str]:
    targets = _resolve_existing_services(services)
    stop(targets)
    time.sleep(0.8)
    return start(targets, config_path=config_path)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def logs(service: str, *, follow: bool = False, lines: int = 50) -> None:
    if service not in SERVICES:
        raise DevManagerError(f"未知服务: {service}")
    path = _log_file(service)
    if not path.exists():
        raise DevManagerError(f"{service} 尚无日志: {path}")

    if follow:
        print(f"[dev] 跟踪 {service} 日志 ({path})，Ctrl+C 退出")
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                chunk = handle.read()
                if chunk:
                    sys.stdout.buffer.write(chunk.encode("utf-8", errors="replace"))
                    sys.stdout.flush()
                time.sleep(0.3)
        return

    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = content[-lines:] if lines > 0 else content
    print(f"[dev] {service} 最近 {len(tail)} 行 ({path})")
    print("-" * 60)
    for line in tail:
        _safe_print(line)


def print_status() -> None:
    rows = status()
    print(f"[dev] 项目目录: {project_root()}")
    print(f"[dev] 状态目录: {dev_dir()}")
    print("-" * 72)
    print(f"{'服务':<8} {'说明':<16} {'运行':<6} {'PID':<8} {'端口':<8} {'地址'}")
    print("-" * 72)
    for row in rows:
        running = "是" if row["running"] else "否"
        pid = str(row["pid"] or "-")
        port = str(row["port"] or "-")
        url = row["url"] or "-"
        print(f"{row['service']:<8} {row['label']:<16} {running:<6} {pid:<8} {port:<8} {url}")
    print("-" * 72)
