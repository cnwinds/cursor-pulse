"""进程内 TTL 缓存：小容量、按存入时间淘汰最旧。

Auto Lender 的 Jev 决策与借用候选白名单都要「同一 key 在 TTL 内复用结果」，
两边各写一套锁 + monotonic + 上限淘汰会慢慢漂移，因此共用这一份。

惰性淘汰往往不触发（键里常含连续变化字段），所以必须有硬上限，否则长驻进程里
只增不减。
"""

from __future__ import annotations

import threading
import time
from typing import Any

DEFAULT_MAX_ENTRIES = 512


class TTLCache:
    """线程安全的 TTL 缓存。

    ``ttl_seconds <= 0`` 表示读失效（``get`` 恒返回 None）；``put`` 始终写入，
    该参数只用于触发过期清理。
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: str, ttl_seconds: float) -> Any | None:
        """取未过期值；已过期或 ``ttl_seconds <= 0`` 时返回 None。"""
        if ttl_seconds <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] > ttl_seconds:
            with self._lock:
                self._entries.pop(key, None)
            return None
        return entry[1]

    def put(self, key: str, value: Any, *, ttl_seconds: float = 0.0) -> None:
        """写入；超上限时先清过期项，再按存入时间丢弃最旧的一批。"""
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            if len(self._entries) > self.max_entries:
                self._sweep_locked(ttl_seconds)

    def clear(self) -> None:
        """清空全部条目。"""
        with self._lock:
            self._entries.clear()

    def pop(self, key: str) -> None:
        """删除单个条目（不存在时无操作）。"""
        with self._lock:
            self._entries.pop(key, None)


    def _sweep_locked(self, ttl_seconds: float) -> None:
        """在持锁状态下清过期条目；仍超上限则按存入时间丢弃最旧的一批。"""
        now = time.monotonic()
        if ttl_seconds > 0:
            for stale in [
                key for key, entry in self._entries.items() if now - entry[0] > ttl_seconds
            ]:
                self._entries.pop(stale, None)
        overflow = len(self._entries) - self.max_entries
        if overflow <= 0:
            return
        oldest = sorted(self._entries.items(), key=lambda item: item[1][0])[:overflow]
        for stale, _ in oldest:
            self._entries.pop(stale, None)
