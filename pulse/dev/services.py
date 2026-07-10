from __future__ import annotations

import shutil
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

    if service == "bot":
        return (
            [*pulse, "-c", config_path, "serve"],
            root,
            {},
        )

    raise KeyError(service)


SERVICES: dict[str, DevService] = {
    "web": DevService("web", "管理后台 API", 8080, "http://127.0.0.1:8080"),
    "admin": DevService("admin", "Vue 开发前端", 5173, "http://127.0.0.1:5173"),
    "bot": DevService("bot", "钉钉机器人 + 调度", None, None),
}

DEFAULT_SERVICES = ("web", "admin", "bot")
