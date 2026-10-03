"""同时在线座位。

进程内内存计座：一个 Web 进程一份账。多进程部署时各进程各自计数。
「一个人」是能解析到的成员。同一成员在同一个账号上的多个会话只占一个座位；
同时用两个账号则各占一席。主负责人直接使用 Cursor 客户端不经过本代理，不计入。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# 进行中的调用若一直收不到结束通知，最多占这么久（兜底，如上游挂死）。
MAX_INFLIGHT_SECONDS = 2 * 3600.0
# 数据面进程（boot id）超过这么久没有任何内部调用，视为已退出；它名下进行中的调用
# 按最后一次出现的时间结束。Go 每 60s 拉一次池，天然就是心跳。
BOOT_STALE_SECONDS = 180.0


@dataclass
class Seat:
    holder_id: str
    account_id: str
    credential_id: str
    seen_at: float
    # boot id → 该数据面进程上进行中的调用数。
    inflight: dict[str, int] = field(default_factory=dict)
    hold_until: float = 0.0
    hold_seconds: float = 0.0

    def alive(self, now: float, ttl_seconds: float) -> bool:
        if any(self.inflight.values()) and now - self.seen_at < MAX_INFLIGHT_SECONDS:
            return True
        if now < self.hold_until:
            return True
        return now - self.seen_at < ttl_seconds


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
        # (holder_id, account_id) → seat. 一人在一个账号上只有一席。
        self._seats: dict[tuple[str, str], Seat] = {}
        # 数据面 boot id → 最后一次内部调用的时间。
        self._boots: dict[str, float] = {}
        self.cp_restored = False

    def reset(self) -> None:
        with self._lock:
            self._seats.clear()
            self._boots.clear()
            self.cp_restored = False

    def note_boot(self, boot_id: str, now: float | None = None) -> None:
        if not boot_id:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            self._boots[boot_id] = now

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
        held_credential_ids: list[str] | None = None,
        now: float | None = None,
    ) -> SeatChoice:
        """在锁内过期、判断并落座。

        ``ranked`` 是偏好顺序的 ``(credential_id, account_id)``。
        ``max_concurrent <= 0`` 表示不限制人数。
        指定账号（``pinned``）始终留在原凭证上，即使已经超过上限。
        ``release_current`` 表示当前凭证不可再用，不会把它分回去。
        ``held_credential_ids`` 是同一人另一个会话槽正在用的凭证：续座，且释放
        current 时若与它同账号，不拆掉这个座位。
        """
        now = time.monotonic() if now is None else now
        current = (current_credential_id or "").strip() or None
        with self._lock:
            self._expire_unlocked(now, ttl_seconds)
            accounts = dict(account_by_credential)
            for cred, account_id in ranked:
                accounts.setdefault(cred, account_id)
            held = [cid for cid in (held_credential_ids or []) if cid]
            held_accounts = {accounts[cid] for cid in held if accounts.get(cid)}

            def holder_on(account_id: str) -> bool:
                return (holder_id, account_id) in self._seats

            def full(account_id: str) -> bool:
                if max_concurrent <= 0 or not account_id:
                    return False
                others = 0
                for (seat_holder, seat_account), _seat in self._seats.items():
                    if seat_holder == holder_id or seat_account != account_id:
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
                    return
                prev = self._seats.get((holder_id, account_id))
                self._seats[(holder_id, account_id)] = Seat(
                    holder_id=holder_id,
                    account_id=account_id,
                    credential_id=credential_id,
                    seen_at=now,
                    inflight=prev.inflight if prev else {},
                    hold_until=prev.hold_until if prev else 0.0,
                    hold_seconds=prev.hold_seconds if prev else 0.0,
                )

            def drop_account(credential_id: str) -> None:
                account_id = accounts.get(credential_id)
                if account_id:
                    if account_id not in held_accounts:
                        self._seats.pop((holder_id, account_id), None)
                    return
                for key, seat in list(self._seats.items()):
                    if key[0] == holder_id and seat.credential_id == credential_id and key[1] not in held_accounts:
                        self._seats.pop(key, None)

            for cid in held:
                occupy(cid)

            if pinned:
                cred = (pinned_credential_id or current or "").strip() or None
                if cred:
                    occupy(cred)
                return SeatChoice(cred, blocked_ids())

            released = (current_credential_id or "").strip() if release_current else ""
            if release_current and released:
                drop_account(released)
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
                return SeatChoice(keep, [])

            if current and accounts.get(current) and (not full(accounts[current]) or holder_on(accounts[current])):
                occupy(current)
                return SeatChoice(current, blocked_ids())

            for cred, account_id in ranked:
                if release_current and cred == released:
                    continue
                if not full(account_id) or holder_on(account_id):
                    occupy(cred)
                    return SeatChoice(cred, blocked_ids())

            return SeatChoice(None, blocked_ids())

    def count_by_account(self, *, ttl_seconds: float, now: float | None = None) -> dict[str, int]:
        """各账号上经代理上报的当前占座人数（已按 TTL 过期清理）。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire_unlocked(now, ttl_seconds)
            counts: dict[str, int] = {}
            for (_holder, account_id), _seat in self._seats.items():
                counts[account_id] = counts.get(account_id, 0) + 1
            return counts

    def begin_call(
        self,
        *,
        holder_id: str,
        account_id: str,
        credential_id: str,
        boot_id: str = "",
        hold_seconds: float = 0.0,
        now: float | None = None,
    ) -> None:
        """一次调用开始：座位在调用结束（或所在数据面进程失联）前不按 TTL 过期。"""
        if not account_id:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if boot_id:
                self._boots[boot_id] = now
            seat = self._seats.get((holder_id, account_id))
            if seat is None:
                seat = Seat(holder_id=holder_id, account_id=account_id, credential_id=credential_id, seen_at=now)
                self._seats[(holder_id, account_id)] = seat
            seat.credential_id = credential_id
            seat.seen_at = now
            seat.hold_seconds = max(seat.hold_seconds, hold_seconds)
            seat.inflight[boot_id] = seat.inflight.get(boot_id, 0) + 1

    def end_call(
        self,
        *,
        holder_id: str,
        account_id: str,
        credential_id: str,
        hold_seconds: float,
        boot_id: str = "",
        recreate: bool = False,
        now: float | None = None,
    ) -> None:
        """一次调用结束：座位再保留 ``hold_seconds``。

        座位已不在（换号释放，或 Web 重启清空）时，只有 ``recreate`` 才补建。
        """
        if not account_id:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if boot_id:
                self._boots[boot_id] = now
            seat = self._seats.get((holder_id, account_id))
            if seat is None:
                if not recreate:
                    return
                seat = Seat(holder_id=holder_id, account_id=account_id, credential_id=credential_id, seen_at=now)
                self._seats[(holder_id, account_id)] = seat
            left = seat.inflight.get(boot_id, 0) - 1
            if left > 0:
                seat.inflight[boot_id] = left
            else:
                seat.inflight.pop(boot_id, None)
            seat.seen_at = now
            seat.hold_until = max(seat.hold_until, now + max(hold_seconds, 0.0))

    def release_idle(self, *, holder_id: str, account_id: str) -> None:
        """换号后释放旧账号上的座位；仍有进行中的调用时保留，等它结束。"""
        with self._lock:
            seat = self._seats.get((holder_id, account_id))
            if seat is not None and not any(seat.inflight.values()):
                self._seats.pop((holder_id, account_id), None)

    def hold(
        self,
        *,
        holder_id: str,
        account_id: str,
        credential_id: str,
        hold_until: float,
        now: float | None = None,
    ) -> None:
        """恢复一个保留到 ``hold_until`` 的座位（Web 重启后从持久化状态回填）。"""
        if not account_id:
            return
        now = time.monotonic() if now is None else now
        if hold_until <= now:
            return
        with self._lock:
            seat = self._seats.get((holder_id, account_id))
            if seat is None:
                seat = Seat(holder_id=holder_id, account_id=account_id, credential_id=credential_id, seen_at=now)
                self._seats[(holder_id, account_id)] = seat
            seat.hold_until = max(seat.hold_until, hold_until)

    def _reap_dead_boots_unlocked(self, now: float) -> None:
        dead = {b: seen for b, seen in self._boots.items() if now - seen >= BOOT_STALE_SECONDS}
        if not dead:
            return
        for seat in self._seats.values():
            for boot_id in [b for b in seat.inflight if b in dead]:
                seat.inflight.pop(boot_id, None)
                seat.hold_until = max(seat.hold_until, dead[boot_id] + seat.hold_seconds)
        for boot_id in dead:
            self._boots.pop(boot_id, None)

    def _expire_unlocked(self, now: float, ttl_seconds: float) -> None:
        self._reap_dead_boots_unlocked(now)
        if ttl_seconds <= 0:
            return
        stale = [key for key, seat in self._seats.items() if not seat.alive(now, ttl_seconds)]
        for key in stale:
            self._seats.pop(key, None)


_book = OccupancyBook()


def get_occupancy() -> OccupancyBook:
    return _book


def reset_occupancy() -> None:
    _book.reset()
