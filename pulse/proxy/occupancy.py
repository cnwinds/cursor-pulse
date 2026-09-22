"""同时在线座位。

进程内内存计座：一个 Web 进程一份账。多进程部署时各进程各自计数。
「一个人」是能解析到的成员；同一成员的多个会话只占一个座位。
主负责人直接使用 Cursor 客户端不经过本代理，不计入。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class Seat:
    holder_id: str
    account_id: str
    credential_id: str
    seen_at: float


@dataclass(frozen=True)
class SeatChoice:
    assigned_credential_id: str | None
    blocked_credential_ids: list[str]
    advised: bool = True


def seat_holder_id(
    *,
    member_id: str | None = None,
    loan_id: str | None = None,
    proxy_key_id: str | None = None,
) -> str:
    """成员优先，否则借用单，否则接入密钥。"""
    if member_id:
        return f"member:{member_id}"
    if loan_id:
        return f"loan:{loan_id}"
    if proxy_key_id:
        return f"pk:{proxy_key_id}"
    return "anon"


class OccupancyBook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_holder: dict[str, Seat] = {}

    def reset(self) -> None:
        with self._lock:
            self._by_holder.clear()

    def choose(
        self,
        *,
        holder_id: str,
        ranked: list[tuple[str, str]],
        account_by_credential: dict[str, str],
        current_credential_id: str | None,
        release_current: bool,
        pinned: bool,
        pinned_credential_id: str | None,
        max_concurrent: int,
        ttl_seconds: float,
        now: float | None = None,
    ) -> SeatChoice:
        """在锁内过期、判断并落座。

        ``ranked`` 是偏好顺序的 ``(credential_id, account_id)``。
        ``max_concurrent <= 0`` 表示不限制人数。
        指定账号（``pinned``）始终留在原凭证上，即使已经超过上限。
        ``release_current`` 表示当前凭证不可再用，不会把它分回去。
        """
        now = time.monotonic() if now is None else now
        current = (current_credential_id or "").strip() or None
        with self._lock:
            self._expire_unlocked(now, ttl_seconds)
            accounts = dict(account_by_credential)
            for cred, account_id in ranked:
                accounts.setdefault(cred, account_id)

            def holder_on(account_id: str) -> bool:
                seat = self._by_holder.get(holder_id)
                return seat is not None and seat.account_id == account_id

            def full(account_id: str) -> bool:
                if max_concurrent <= 0 or not account_id:
                    return False
                others = 0
                for seat in self._by_holder.values():
                    if seat.holder_id == holder_id or seat.account_id != account_id:
                        continue
                    others += 1
                    if others >= max_concurrent:
                        return True
                return False

            def blocked_ids() -> list[str]:
                if max_concurrent <= 0:
                    return []
                out: list[str] = []
                seen: set[str] = set()
                for cred, account_id in ranked:
                    if cred in seen:
                        continue
                    if full(account_id) and not holder_on(account_id):
                        out.append(cred)
                        seen.add(cred)
                for cred, account_id in accounts.items():
                    if cred in seen:
                        continue
                    if full(account_id) and not holder_on(account_id):
                        out.append(cred)
                        seen.add(cred)
                return out

            def occupy(credential_id: str) -> None:
                account_id = accounts.get(credential_id)
                if not account_id:
                    self._by_holder.pop(holder_id, None)
                    return
                self._by_holder[holder_id] = Seat(
                    holder_id=holder_id,
                    account_id=account_id,
                    credential_id=credential_id,
                    seen_at=now,
                )

            if pinned:
                cred = (pinned_credential_id or current or "").strip() or None
                if cred:
                    occupy(cred)
                return SeatChoice(cred, blocked_ids())

            released = (current_credential_id or "").strip() if release_current else ""
            if release_current:
                self._by_holder.pop(holder_id, None)
                current = None

            if max_concurrent <= 0:
                keep = current if current and accounts.get(current) else None
                if keep is None:
                    for cred, _account_id in ranked:
                        if cred != released:
                            keep = cred
                            break
                if keep:
                    occupy(keep)
                else:
                    self._by_holder.pop(holder_id, None)
                return SeatChoice(keep, [])

            if current and accounts.get(current) and (
                not full(accounts[current]) or holder_on(accounts[current])
            ):
                occupy(current)
                return SeatChoice(current, blocked_ids())

            for cred, account_id in ranked:
                if release_current and cred == released:
                    continue
                if not full(account_id) or holder_on(account_id):
                    occupy(cred)
                    return SeatChoice(cred, blocked_ids())

            self._by_holder.pop(holder_id, None)
            return SeatChoice(None, blocked_ids())

    def _expire_unlocked(self, now: float, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        stale = [
            holder
            for holder, seat in self._by_holder.items()
            if now - seat.seen_at >= ttl_seconds
        ]
        for holder in stale:
            self._by_holder.pop(holder, None)


_book = OccupancyBook()


def get_occupancy() -> OccupancyBook:
    return _book


def reset_occupancy() -> None:
    _book.reset()
