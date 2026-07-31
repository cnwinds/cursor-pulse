"""Shared reads for account quota snapshots (Credential Pool / loans / board)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.repository import ToolCenterRepository


def latest_snapshots_for_accounts(
    session: Session,
    account_ids: Iterable[str],
) -> dict[str, AccountQuotaSnapshot]:
    """Bulk-load the newest snapshot per account id."""
    ids = list({aid for aid in account_ids if aid})
    if not ids:
        return {}
    latest: dict[str, AccountQuotaSnapshot] = {}
    for snap in session.execute(
        select(AccountQuotaSnapshot)
        .where(AccountQuotaSnapshot.account_id.in_(ids))
        .order_by(AccountQuotaSnapshot.captured_at.desc())
    ).scalars():
        if snap.account_id not in latest:
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
    repo = ToolCenterRepository(session, team_id)
    accounts = repo.list_active_accounts() if active_only else repo.list_accounts()
    if vendor_slug is not None:
        account_ids = [
            a.id for a in accounts if a.vendor and a.vendor.slug == vendor_slug
        ]
    else:
        account_ids = [a.id for a in accounts]
    return latest_snapshots_for_accounts(session, account_ids)
