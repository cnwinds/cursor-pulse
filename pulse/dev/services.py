from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DevService:
    name: str
    label: str
    port: int | None
    url: str | None


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dev_dir() -> Path:
    return project_root() / ".dev"


def logs_dir() -> Path:
    return dev_dir() / "logs"


def pids_dir() -> Path:
    return dev_dir() / "pids"


def pulse_executable(root: Path | None = None) -> list[str]:
    root = root or project_root()
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "pulse.exe"
    else:
        candidate = root / ".venv" / "bin" / "pulse"
    if candidate.exists():
        return [str(candidate)]
    found = shutil.which("pulse")
    if found:
        return [found]
    return [sys.executable, "-m", "pulse.cli"]


def npm_executable() -> str:
    for name in ("npm", "npm.cmd"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("未找到 npm，请先安装 Node.js")


def proxy_binary_name() -> str:
    return "cursor-pulse-proxy.exe" if sys.platform == "win32" else "cursor-pulse-proxy"


def _find_go() -> str | None:
    found = shutil.which("go")
    if found:
        return found
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Go" / "bin" / "go.exe"
    if candidate.exists():
        return str(candidate)
    return None


def ensure_proxy_binary(root: Path | None = None) -> Path:
    """Return path to built proxy binary; build/rebuild when missing or stale."""
    root = root or project_root()
    proxy_dir = root / "proxy"
    binary = proxy_dir / proxy_binary_name()
    go = _find_go()

    needs_build = not binary.exists()
    if not needs_build:
        bin_mtime = binary.stat().st_mtime
        for src in proxy_dir.glob("*.go"):
            if src.stat().st_mtime > bin_mtime:
                needs_build = True
                break

    if not needs_build:
        return binary

    if not go:
        if binary.exists():
            return binary
        raise FileNotFoundError(
            "未找到 Go 代理二进制且未安装 go。请安装 Go 1.22+ 后执行: "
            f"cd proxy && go build -o {proxy_binary_name()} ."
        )
    subprocess.run([go, "build", "-o", str(binary), "."], cwd=str(proxy_dir), check=True)
    return binary


def build_command(service: str, *, config_path: str = "config.yaml") -> tuple[list[str], Path, dict]:
    """Return (command argv, working directory, popen kwargs extras)."""
    root = project_root()
    pulse = pulse_executable(root)

    if service == "web":
        return (
            [*pulse, "-c", config_path, "web", "--reload"],
            root,
            {},
        )

    if service == "admin":
        return (
            [npm_executable(), "run", "dev"],
            root / "web-admin",
            {"shell": sys.platform == "win32"},
        )

    if service == "channel":
        return (
            [*pulse, "-c", config_path, "channel", "--reload"],
            root,
            {},
        )

    if service == "assistant":
        return (
            [sys.executable, "-m", "assistant_platform", "serve", "--reload"],
            root,
            {},
        )

    if service == "proxy":
        binary = ensure_proxy_binary(root)
        return (
            [str(binary), "-listen", "0.0.0.0:8317"],
            root / "proxy",
            {},
        )

    raise KeyError(service)


SERVICES: dict[str, DevService] = {
    "web": DevService("web", "管理后台 API", 8080, "http://127.0.0.1:8080"),
    "admin": DevService("admin", "Vue 开发前端", 5173, "http://127.0.0.1:5173"),
    "channel": DevService("channel", "渠道适配 + 调度", None, None),
    "assistant": DevService("assistant", "Assistant Platform", 8090, "http://127.0.0.1:8090"),
    "proxy": DevService("proxy", "Cursor 代理（Go 数据面）", 8317, "http://0.0.0.0:8317"),
}

DEFAULT_SERVICES = ("web", "admin", "channel", "assistant")
