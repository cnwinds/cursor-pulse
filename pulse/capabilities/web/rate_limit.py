"""In-memory sliding-window rate limiter for web.search / web.fetch."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _SlidingWindow:
    limit: int
    window_seconds: float = 60.0
    _events: deque[float] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def configure(self, limit: int) -> None:
        self.limit = max(0, int(limit))

    def try_acquire(self, *, now: float | None = None) -> tuple[bool, int | None]:
        """Return (allowed, retry_after_seconds). limit <= 0 disables throttling."""
        if self.limit <= 0:
            return True, None
        ts = time.monotonic() if now is None else now
        cutoff = ts - self.window_seconds
        with self._lock:
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()
            if len(self._events) >= self.limit:
                retry_after = max(1, math.ceil(self.window_seconds - (ts - self._events[0])))
                return False, retry_after
            self._events.append(ts)
            return True, None


class WebRateLimitRegistry:
    """Per-team limiters; limit is refreshed from config on each check (hot reload)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_team: dict[str, _SlidingWindow] = {}

    def check(self, team_id: str, limit_per_minute: int) -> tuple[bool, int | None]:
        key = team_id or "__global__"
        with self._lock:
            limiter = self._by_team.get(key)
            if limiter is None:
                limiter = _SlidingWindow(limit=limit_per_minute)
                self._by_team[key] = limiter
            else:
                limiter.configure(limit_per_minute)
        return limiter.try_acquire()


_registry = WebRateLimitRegistry()


def check_web_rate_limit(team_id: str, limit_per_minute: int) -> tuple[bool, int | None]:
    return _registry.check(team_id, limit_per_minute)


def reset_web_rate_limits() -> None:
    """Test helper: clear accumulated counters."""
    with _registry._lock:
        _registry._by_team.clear()
