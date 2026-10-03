"""Sticky Coding Plan account selection per pkcp_ key (Switch dwell + load balance)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pulse.openai_proxy.pool import cp_rank_key, list_cp_pool_entries
from pulse.proxy.clock import utcnow
from pulse.proxy.occupancy import get_occupancy, seat_holder_id
from pulse.settings.team_store import effective_loan_selection
from pulse.storage.models import CpOpenAiStickyBinding
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _dwell_active(*, last_active_at: datetime | None, min_switch_minutes: float, now: datetime) -> bool:
    """True while the idle gap since the last request is inside the dwell window."""
    if min_switch_minutes <= 0 or last_active_at is None:
        return False
    return (now - _aware(last_active_at)) < timedelta(minutes=min_switch_minutes)


def _entry_map(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["credential_id"]: e for e in entries if e.get("credential_id")}


def _rank_for_balance(entries: list[dict[str, Any]], *, account_load: dict[str, int]) -> list[dict[str, Any]]:
    """Higher score (lower tier pressure) and fewer concurrent proxy seats first."""

    def sort_key(e: dict[str, Any]) -> tuple:
        return cp_rank_key(
            pressure=e.get("tier_pressure_pct"),
            load=account_load.get(e.get("account_id") or "", 0),
            identifier=e.get("account_identifier"),
        )

    return sorted(entries, key=sort_key)


def _upsert_binding(
    session: Session,
    *,
    proxy_key_id: str,
    credential_id: str,
    sticky_since: datetime,
    now: datetime,
) -> None:
    row = session.get(CpOpenAiStickyBinding, proxy_key_id)
    if row is None:
        session.add(
            CpOpenAiStickyBinding(
                proxy_key_id=proxy_key_id,
                credential_id=credential_id,
                sticky_since=sticky_since,
                updated_at=now,
            )
        )
        return
    if row.credential_id != credential_id:
        row.credential_id = credential_id
        row.sticky_since = sticky_since
    row.updated_at = now


def resolve_cp_credential(
    session: Session,
    *,
    proxy_key_id: str,
    vendor_slug: str,
    encryption_key: str,
    exclude_credential_ids: set[str] | None = None,
    current_credential_id: str | None = None,
    release_current: bool = False,
    config,
    team_id: str | None,
    boot_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Pick pool credential with per-key sticky dwell, then quota + concurrency balance."""
    exclude = set(exclude_credential_ids or [])
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    restore_cp_seats(session, config=config, now=now)

    entries = list_cp_pool_entries(
        session,
        vendor_slug=vendor_slug,
        encryption_key=encryption_key,
        include_api_keys=True,
    )
    available = [e for e in entries if e.get("credential_id") not in exclude and e.get("api_key")]
    if not available:
        return None

    by_id = _entry_map(available)
    selection = effective_loan_selection(session, config, team_id)
    min_switch = float(selection.min_switch_minutes or 0)
    holder = seat_holder_id(proxy_key_id=proxy_key_id)

    binding = session.get(CpOpenAiStickyBinding, proxy_key_id)
    sticky_id = (binding.credential_id if binding else "") or ""
    sticky_since = binding.sticky_since if binding else None
    last_active_at = binding.updated_at if binding else None

    current = (current_credential_id or "").strip() or None
    if release_current and current:
        if binding and binding.credential_id == current:
            session.delete(binding)
            binding = None
            sticky_id = ""
            sticky_since = None
            last_active_at = None
        get_occupancy().choose(
            holder_id=holder,
            ranked=[(current, by_id.get(current, {}).get("account_id", ""))],
            account_by_credential={current: by_id.get(current, {}).get("account_id", "")},
            current_credential_id=current,
            release_current=True,
            pinned=False,
            pinned_credential_id=None,
            max_concurrent=int(selection.max_concurrent_users or 0),
            ttl_seconds=float(selection.concurrent_ttl_seconds or 180),
        )

    # Cache hit: same account within Switch dwell (unless excluded / left pool).
    if (
        sticky_id
        and sticky_id in by_id
        and sticky_id not in exclude
        and _dwell_active(last_active_at=last_active_at, min_switch_minutes=min_switch, now=now)
    ):
        chosen_id = sticky_id
        _touch_seat(holder, chosen_id, by_id, selection)
        _upsert_binding(
            session,
            proxy_key_id=proxy_key_id,
            credential_id=chosen_id,
            sticky_since=sticky_since or now,
            now=now,
        )
        _begin_call(holder, by_id[chosen_id], selection=selection, boot_id=boot_id)
        return by_id[chosen_id]

    account_load = get_occupancy().count_by_account(
        ttl_seconds=float(selection.concurrent_ttl_seconds or 180),
    )
    ranked_entries = _rank_for_balance(available, account_load=account_load)
    ranked_pairs = [(e["credential_id"], e["account_id"]) for e in ranked_entries]
    account_by_cred = {e["credential_id"]: e["account_id"] for e in ranked_entries}

    # Dwell expired (or no binding): re-rank from scratch. The gateway's remembered
    # current credential must not win here, or a live proxy would never rotate.
    choice = get_occupancy().choose(
        holder_id=holder,
        ranked=ranked_pairs,
        account_by_credential=account_by_cred,
        current_credential_id=None,
        release_current=False,
        pinned=False,
        pinned_credential_id=None,
        max_concurrent=int(selection.max_concurrent_users or 0),
        ttl_seconds=float(selection.concurrent_ttl_seconds or 180),
    )
    chosen_id = choice.assigned_credential_id
    blocked = set(choice.blocked_credential_ids or [])
    if not chosen_id:
        for entry in ranked_entries:
            cid = entry["credential_id"]
            if cid in blocked:
                continue
            chosen_id = cid
            break
    if not chosen_id or chosen_id not in by_id:
        return None

    new_since = sticky_since or now
    if chosen_id != sticky_id:
        new_since = now
        logger.info(
            "cp openai sticky rotate key=%s cred %s -> %s (dwell expired or failover)",
            proxy_key_id[:8],
            sticky_id[:8] if sticky_id else "-",
            chosen_id[:8],
        )
    chosen_account = by_id[chosen_id]["account_id"]
    for old_cred in {sticky_id, current or ""} - {"", chosen_id}:
        old_account = account_by_cred.get(old_cred)
        if old_account and old_account != chosen_account:
            get_occupancy().release_idle(holder_id=holder, account_id=old_account)
    _upsert_binding(
        session,
        proxy_key_id=proxy_key_id,
        credential_id=chosen_id,
        sticky_since=new_since,
        now=now,
    )
    session.flush()
    _begin_call(holder, by_id[chosen_id], selection=selection, boot_id=boot_id)
    return by_id[chosen_id]


def _hold_seconds(selection) -> float:
    """Seat hold after a call ends: the Switch dwell window (never shorter than the seat TTL)."""
    return max(
        float(selection.min_switch_minutes or 0) * 60.0,
        float(selection.concurrent_ttl_seconds or 180),
    )


def end_cp_call(
    session: Session,
    *,
    proxy_key_id: str,
    credential_id: str,
    config,
    team_id: str | None,
    boot_id: str = "",
    now: datetime | None = None,
) -> None:
    """Gateway call finished: keep the seat (and sticky dwell) for the window after it.

    A seat that is gone is rebuilt only while the sticky binding still points at this
    credential (Web restart); after a failover the binding moved and the old seat stays released.
    """
    from pulse.storage.models import AiAccountCredential

    cred = session.get(AiAccountCredential, credential_id) if credential_id else None
    if cred is None:
        return
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    binding = session.get(CpOpenAiStickyBinding, proxy_key_id)
    still_bound = binding is not None and binding.credential_id == credential_id
    if still_bound:
        binding.updated_at = now
    selection = effective_loan_selection(session, config, team_id)
    get_occupancy().end_call(
        holder_id=seat_holder_id(proxy_key_id=proxy_key_id),
        account_id=cred.account_id,
        credential_id=credential_id,
        hold_seconds=_hold_seconds(selection),
        boot_id=boot_id,
        recreate=still_bound,
    )


def restore_cp_seats(session: Session, *, config, now: datetime | None = None) -> None:
    """Once per Web process: rebuild gateway seats from sticky bindings still inside the hold window."""
    import time

    from pulse.storage.models import AiAccountCredential, Member, ProxyKey

    book = get_occupancy()
    if book.cp_restored:
        return
    book.cp_restored = True
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    mono = time.monotonic()
    for binding in session.query(CpOpenAiStickyBinding).all():
        key = session.get(ProxyKey, binding.proxy_key_id)
        cred = session.get(AiAccountCredential, binding.credential_id)
        if key is None or cred is None or key.status != "active":
            continue
        member = session.get(Member, key.member_id)
        selection = effective_loan_selection(session, config, member.team_id if member else None)
        idle = (now - _aware(binding.updated_at)).total_seconds()
        book.hold(
            holder_id=seat_holder_id(proxy_key_id=key.id),
            account_id=cred.account_id,
            credential_id=cred.id,
            hold_until=mono + _hold_seconds(selection) - idle,
            now=mono,
        )


def _begin_call(holder: str, entry: dict[str, Any], *, selection, boot_id: str) -> None:
    get_occupancy().begin_call(
        holder_id=holder,
        account_id=entry.get("account_id") or "",
        credential_id=entry["credential_id"],
        boot_id=boot_id,
        hold_seconds=_hold_seconds(selection),
    )


def _touch_seat(
    holder: str,
    credential_id: str,
    by_id: dict[str, dict[str, Any]],
    selection,
) -> None:
    entry = by_id.get(credential_id)
    if not entry:
        return
    get_occupancy().choose(
        holder_id=holder,
        ranked=[(credential_id, entry["account_id"])],
        account_by_credential={credential_id: entry["account_id"]},
        current_credential_id=credential_id,
        release_current=False,
        pinned=False,
        pinned_credential_id=None,
        max_concurrent=int(selection.max_concurrent_users or 0),
        ttl_seconds=float(selection.concurrent_ttl_seconds or 180),
    )
