"""Shared dev hot-reload watch paths."""

from __future__ import annotations

from pathlib import Path

_RELOAD_PACKAGES = ("pulse", "assistant_platform")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def python_reload_dirs() -> list[str]:
    root = project_root()
    return [str(root / name) for name in _RELOAD_PACKAGES if (root / name).is_dir()]
