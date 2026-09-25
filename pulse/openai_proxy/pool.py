"""Coding Plan credential pool for OpenAI gateway."""

from __future__ import annotations

import logging
from typing import Any

from pulse.openai_proxy.upstream import CP_VENDORS
from pulse.tool_center.quota_reads import latest_snapshots_for_accounts
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_TIER_PCT = 95.0


def _tier_pressure(snap) -> float:
    if snap is None:
        return 50.0
    if snap.total_pct is not None:
        return float(snap.total_pct)
    extra = snap.quota_extra if isinstance(snap.quota_extra, dict) else {}
    tiers = extra.get("tiers") if isinstance(extra.get("tiers"), list) else []
    pcts: list[float] = []
    for t in tiers:
        if isinstance(t, dict) and t.get("used_pct") is not None:
            pcts.append(float(t["used_pct"]))
    return max(pcts) if pcts else 50.0


def list_cp_pool_entries(
    session: Session,
    *,
    vendor_slug: str,
    encryption_key: str,
    include_api_keys: bool = False,
) -> list[dict[str, Any]]:
    from pulse.ingestion.crypto import decrypt_secret
    from pulse.storage.models import AiAccount, AiAccountCredential, AiVendor

    slug = vendor_slug.strip().lower()
    if slug not in CP_VENDORS:
        return []

    rows = session.execute(
        select(AiAccountCredential, AiAccount, AiVendor)
        .join(AiAccount, AiAccountCredential.account_id == AiAccount.id)
        .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
        .where(
            AiVendor.slug == slug,
            AiVendor.is_active.is_(True),
            AiAccount.cp_proxy_enabled.is_(True),
            AiAccount.deleted_at.is_(None),
            AiAccountCredential.status == "active",
            AiAccountCredential.key_role == "primary",
        )
        .order_by(AiAccountCredential.bound_at)
    ).all()
    if not rows:
        return []

    account_ids = list({acc.id for _cred, acc, _v in rows})
    snaps = latest_snapshots_for_accounts(session, account_ids)

    seen: set[str] = set()
    candidates: list[tuple[float, AiAccountCredential, AiAccount]] = []
    for cred, acc, _vendor in rows:
        if acc.id in seen:
            continue
        seen.add(acc.id)
        pressure = _tier_pressure(snaps.get(acc.id))
        if pressure >= MAX_TIER_PCT:
            continue
        candidates.append((pressure, cred, acc))

    candidates.sort(key=lambda x: (x[0], x[2].account_identifier))
    enc = encryption_key.strip()
    out: list[dict[str, Any]] = []
    for pressure, cred, acc in candidates:
        item: dict[str, Any] = {
            "credential_id": cred.id,
            "account_id": acc.id,
            "account_identifier": acc.account_identifier,
            "api_region": acc.api_region,
            "tier_pressure_pct": pressure,
        }
        if include_api_keys and enc:
            try:
                item["api_key"] = decrypt_secret(cred.encrypted_value, enc)
            except Exception:
                logger.warning("cp pool: skip credential %s (decrypt failed)", cred.id)
                continue
        out.append(item)
    return out


def list_cp_admin_accounts(session: Session, *, vendor_slug: str) -> list[dict[str, Any]]:
    """All Coding Plan accounts for admin UI (入池开关 + 额度压力)."""
    from pulse.storage.models import AiAccount, AiAccountCredential, AiVendor

    slug = vendor_slug.strip().lower()
    if slug not in CP_VENDORS:
        return []
    accounts = (
        session.execute(
            select(AiAccount)
            .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
            .where(
                AiVendor.slug == slug,
                AiAccount.deleted_at.is_(None),
            )
            .order_by(AiAccount.account_identifier)
        )
        .scalars()
        .all()
    )
    if not accounts:
        return []
    account_ids = [a.id for a in accounts]
    creds = (
        session.execute(select(AiAccountCredential).where(AiAccountCredential.account_id.in_(account_ids)))
        .scalars()
        .all()
    )
    active_primary: dict[str, int] = {}
    for c in creds:
        if c.status == "active" and c.key_role == "primary":
            active_primary[c.account_id] = active_primary.get(c.account_id, 0) + 1
    snaps = latest_snapshots_for_accounts(session, account_ids)
    out: list[dict[str, Any]] = []
    for acc in accounts:
        n = active_primary.get(acc.id, 0)
        pool_ready = n == 1
        pool_ready_reason = None
        if n == 0:
            pool_ready_reason = "无可用主 Key"
        elif n > 1:
            pool_ready_reason = "存在多个主 Key"
        pressure = _tier_pressure(snaps.get(acc.id))
        out.append(
            {
                "id": acc.id,
                "account_identifier": acc.account_identifier,
                "api_region": acc.api_region,
                "cp_proxy_enabled": bool(acc.cp_proxy_enabled),
                "pool_ready": pool_ready,
                "pool_ready_reason": pool_ready_reason,
                "tier_pressure_pct": pressure,
                "pool_effective": bool(acc.cp_proxy_enabled and pool_ready and pressure < MAX_TIER_PCT),
            }
        )
    return out


def pick_cp_credential(
    session: Session,
    *,
    vendor_slug: str,
    encryption_key: str,
    exclude_credential_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    exclude = exclude_credential_ids or set()
    entries = list_cp_pool_entries(
        session,
        vendor_slug=vendor_slug,
        encryption_key=encryption_key,
        include_api_keys=True,
    )
    for entry in entries:
        if entry["credential_id"] in exclude:
            continue
        if entry.get("api_key"):
            return entry
    return None
