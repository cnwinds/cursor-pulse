"""Sticky Coding Plan account selection per pkcp_ key (Switch dwell + load balance)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pulse.openai_proxy.pool import list_cp_pool_entries
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


def _dwell_active(*, sticky_since: datetime | None, min_switch_minutes: float, now: datetime) -> bool:
    if min_switch_minutes <= 0 or sticky_since is None:
        return False
    return (now - _aware(sticky_since)) < timedelta(minutes=min_switch_minutes)


def _entry_map(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["credential_id"]: e for e in entries if e.get("credential_id")}


def _rank_for_balance(entries: list[dict[str, Any]], *, account_load: dict[str, int]) -> list[dict[str, Any]]:
    """Lower tier pressure and fewer concurrent proxy seats first."""

    def sort_key(e: dict[str, Any]) -> tuple:
        acc = e.get("account_id") or ""
        return (
            float(e.get("tier_pressure_pct") or 50.0),
            int(account_load.get(acc, 0)),
            str(e.get("account_identifier") or ""),
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
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Pick pool credential with per-key sticky dwell, then quota + concurrency balance."""
    exclude = set(exclude_credential_ids or [])
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

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

    current = (current_credential_id or "").strip() or None
    if release_current and current:
        if binding and binding.credential_id == current:
            session.delete(binding)
            binding = None
            sticky_id = ""
            sticky_since = None
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
        and _dwell_active(sticky_since=sticky_since, min_switch_minutes=min_switch, now=now)
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
        return by_id[chosen_id]

    account_load = get_occupancy().count_by_account(
        ttl_seconds=float(selection.concurrent_ttl_seconds or 180),
    )
    ranked_entries = _rank_for_balance(available, account_load=account_load)
    ranked_pairs = [(e["credential_id"], e["account_id"]) for e in ranked_entries]
    account_by_cred = {e["credential_id"]: e["account_id"] for e in ranked_entries}

    choice = get_occupancy().choose(
        holder_id=holder,
        ranked=ranked_pairs,
        account_by_credential=account_by_cred,
        current_credential_id=current if current in by_id else None,
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
    _upsert_binding(
        session,
        proxy_key_id=proxy_key_id,
        credential_id=chosen_id,
        sticky_since=new_since,
        now=now,
    )
    session.flush()
    return by_id[chosen_id]


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


