"""Simple in-memory rate limit for password login attempts."""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
# key -> list of attempt timestamps (unix seconds)
_ATTEMPTS: dict[str, list[float]] = {}

_WINDOW_SEC = 300
_MAX_ATTEMPTS = 20


def check_login_allowed(*, ip: str | None, username: str) -> str | None:
    """Return an error message if the attempt should be rejected, else None."""
    now = time.time()
    keys = [f"user:{(username or '').strip().lower()}"]
    if ip:
        keys.append(f"ip:{ip}")
    with _LOCK:
        for key in keys:
            stamps = [t for t in _ATTEMPTS.get(key, []) if now - t < _WINDOW_SEC]
            _ATTEMPTS[key] = stamps
            if len(stamps) >= _MAX_ATTEMPTS:
                return "登录尝试过于频繁，请稍后再试"
    return None


def record_login_attempt(*, ip: str | None, username: str) -> None:
    now = time.time()
    keys = [f"user:{(username or '').strip().lower()}"]
    if ip:
        keys.append(f"ip:{ip}")
    with _LOCK:
        for key in keys:
            stamps = [t for t in _ATTEMPTS.get(key, []) if now - t < _WINDOW_SEC]
            stamps.append(now)
            _ATTEMPTS[key] = stamps
