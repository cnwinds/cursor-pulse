"""Shared reads for account quota snapshots (Credential Pool / loans / board)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pulse.storage.models import AccountQuotaSnapshot, AiAccount, AiVendor

_IN_CHUNK = 400
SNAPSHOT_KEEP_PER_ACCOUNT = 48


def latest_snapshots_for_accounts(
    session: Session,
    account_ids: Iterable[str],
) -> dict[str, AccountQuotaSnapshot]:
    """Bulk-load the newest snapshot per account id.

    Uses MAX(captured_at) per account instead of loading snapshot history.
    """
    ids = list({aid for aid in account_ids if aid})
    if not ids:
        return {}
    latest: dict[str, AccountQuotaSnapshot] = {}
    for offset in range(0, len(ids), _IN_CHUNK):
        latest.update(_latest_snapshots_chunk(session, ids[offset : offset + _IN_CHUNK]))
    return latest


def _latest_snapshots_chunk(
    session: Session, ids: list[str]
) -> dict[str, AccountQuotaSnapshot]:
    latest_at = (
        select(
            AccountQuotaSnapshot.account_id.label("account_id"),
            func.max(AccountQuotaSnapshot.captured_at).label("captured_at"),
        )
        .where(AccountQuotaSnapshot.account_id.in_(ids))
        .group_by(AccountQuotaSnapshot.account_id)
        .subquery()
    )
    latest: dict[str, AccountQuotaSnapshot] = {}
    for snap in session.scalars(
        select(AccountQuotaSnapshot).join(
            latest_at,
            (AccountQuotaSnapshot.account_id == latest_at.c.account_id)
            & (AccountQuotaSnapshot.captured_at == latest_at.c.captured_at),
        )
    ):
        existing = latest.get(snap.account_id)
        if existing is None or snap.id > existing.id:
            latest[snap.account_id] = snap
    return latest


def latest_snapshots_for_team(
    session: Session,
    team_id: str,
    *,
    vendor_slug: str | None = "cursor",
    active_only: bool = True,
) -> dict[str, AccountQuotaSnapshot]:
    """Newest snapshots for a team's accounts.

    Default ``vendor_slug=\"cursor\"`` matches loan / Credential Pool Intake.
    Pass ``vendor_slug=None`` for all vendors. ``active_only`` mirrors
    ToolCenterRepository active-status filtering.
    """
    from pulse.tool_center.repository import ACTIVE_ACCOUNT_STATUSES

    query = select(AiAccount.id).where(
        AiAccount.team_id == team_id, AiAccount.deleted_at.is_(None)
    )
    if active_only:
        query = query.where(AiAccount.status.in_(ACTIVE_ACCOUNT_STATUSES))
    if vendor_slug is not None:
        query = query.where(
            AiAccount.vendor_id.in_(select(AiVendor.id).where(AiVendor.slug == vendor_slug))
        )
    return latest_snapshots_for_accounts(session, session.scalars(query).all())


def prune_quota_snapshots_for_account(
    session: Session,
    account_id: str,
    *,
    keep: int = SNAPSHOT_KEEP_PER_ACCOUNT,
) -> int:
    """Drop older snapshots for one account, keeping the newest ``keep`` rows."""
    if keep < 1 or not account_id:
        return 0
    cutoff = session.scalar(
        select(AccountQuotaSnapshot.captured_at)
        .where(AccountQuotaSnapshot.account_id == account_id)
        .order_by(AccountQuotaSnapshot.captured_at.desc())
        .offset(keep - 1)
        .limit(1)
    )
    if cutoff is None:
        return 0
    result = session.execute(
        delete(AccountQuotaSnapshot).where(
            AccountQuotaSnapshot.account_id == account_id,
            AccountQuotaSnapshot.captured_at < cutoff,
        )
    )
    return int(result.rowcount or 0)
