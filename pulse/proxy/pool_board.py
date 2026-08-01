"""Credential Pool Board — intake ranking for the MITM Credential Pool.

Orchestrates Quota Snapshot Read → LenderCandidate assembly → burn_rate scoring
(enforce_loan_cap=False applies Snapshot Headroom OR). Callers should not
re-implement intake filters.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.tool_center.burn_rate import LenderCandidate

logger = logging.getLogger(__name__)


def _pool_primary_context(session: Session) -> tuple[list, dict, dict, dict]:
    """入池 primary 凭证上下文：(creds, accounts_by_id, latest_snaps, loan_counts)。"""
    from pulse.storage.models import (
        AiAccount,
        AiAccountCredential,
        AiVendor,
        KeyLoan,
    )
    from pulse.tool_center.quota_reads import latest_snapshots_for_accounts

    rows = (
        session.execute(
            select(AiAccountCredential)
            .join(AiVendor, AiAccountCredential.vendor_id == AiVendor.id)
            .join(AiAccount, AiAccountCredential.account_id == AiAccount.id)
            .where(
                AiVendor.slug == "cursor",
                AiVendor.is_active.is_(True),
                AiAccount.proxy_enabled.is_(True),
                AiAccount.deleted_at.is_(None),
                AiAccountCredential.status == "active",
                AiAccountCredential.key_role == "primary",
            )
            .order_by(AiAccountCredential.bound_at)
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], {}, {}, {}

    # 每账号仅保留最早绑定的一个 primary（防御性；入池前应在 API 层禁止多 primary）
    seen_accounts: set[str] = set()
    unique_rows = []
    for cred in rows:
        if cred.account_id in seen_accounts:
            continue
        seen_accounts.add(cred.account_id)
        unique_rows.append(cred)
    rows = unique_rows

    account_ids = list({c.account_id for c in rows})
    accounts = {
        a.id: a
        for a in session.execute(
            select(AiAccount).where(AiAccount.id.in_(account_ids))
        ).scalars()
    }
    latest_snaps = latest_snapshots_for_accounts(session, account_ids)

    loan_counts: dict = dict(
        session.execute(
            select(KeyLoan.source_account_id, func.count())
            .where(
                KeyLoan.source_account_id.in_(account_ids),
                KeyLoan.status == "active",
            )
            .group_by(KeyLoan.source_account_id)
        ).all()
    )
    return rows, accounts, latest_snaps, loan_counts


def _pool_scoring_clock(latest_snaps: dict) -> tuple[date, datetime]:
    """与配额快照 captured_at 对齐的 today/now，避免墙钟与快照数据脱节。"""
    captured: list[datetime] = []
    for snap in latest_snaps.values():
        if snap.captured_at is None:
            continue
        t = snap.captured_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        captured.append(t)
    now = max(captured) if captured else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.date(), now


def _build_pool_lender_candidates(
    accounts: dict,
    latest_snaps: dict,
    loan_counts: dict,
    account_ids: set[str],
    *,
    include_no_snap_excluded: bool,
) -> tuple[list[LenderCandidate], list[dict]]:
    """Assemble LenderCandidates; optionally collect no_snapshot exclusions.

    Snapshot Headroom is applied by burn_rate (enforce_loan_cap=False). Accounts
    that already fail ``snapshot_has_any_pool_headroom`` are still passed through
    so explain_lender_selection can surface the reason.
    """
    candidates: list[LenderCandidate] = []
    excluded_no_snap: list[dict] = []
    for aid in sorted(account_ids):
        account = accounts.get(aid)
        if not account:
            continue
        snap = latest_snaps.get(aid)
        active_loans = loan_counts.get(aid, 0)
        if not snap:
            if include_no_snap_excluded:
                excluded_no_snap.append(
                    {
                        "account_id": aid,
                        "account_identifier": account.account_identifier,
                        "reason": "no_snapshot",
                        "active_loans": active_loans,
                        "status": None,
                        "deadline": None,
                        "hours_to_deadline": None,
                        "renews_on": account.renews_on.isoformat() if account.renews_on else None,
                        "remaining_headroom_pct": None,
                        "total_pct": None,
                    }
                )
            continue
        candidates.append(
            LenderCandidate(
                snapshot=snap,
                account_id=aid,
                account_identifier=account.account_identifier,
                renews_on=account.renews_on,
                active_loans=active_loans,
            )
        )
    return candidates, excluded_no_snap


def list_pool_credentials(
    session: Session,
    *,
    encryption_key: str,
    loan_selection=None,
) -> list[dict]:
    """Credential Pool Board → decryptable primary credentials (ranked)."""
    from pulse.ingestion.crypto import decrypt_secret
    from pulse.storage.models import AiAccountCredential
    from pulse.tool_center.burn_rate import recommend_lenders

    rows, accounts, latest_snaps, loan_counts = _pool_primary_context(session)
    if not rows:
        return []

    candidates, _ = _build_pool_lender_candidates(
        accounts,
        latest_snaps,
        loan_counts,
        {c.account_id for c in rows},
        include_no_snap_excluded=False,
    )
    today, now = _pool_scoring_clock(latest_snaps)
    ranked = recommend_lenders(
        candidates,
        today,
        loan_selection=loan_selection,
        now=now,
        enforce_loan_cap=False,
    )
    ranked_ids = [item["account_id"] for item in ranked]
    allowed = set(ranked_ids)

    by_account: dict[str, list[AiAccountCredential]] = {}
    for cred in rows:
        if cred.account_id not in allowed:
            continue
        by_account.setdefault(cred.account_id, []).append(cred)

    enc_key = (encryption_key or "").strip()
    out: list[dict] = []
    for aid in ranked_ids:
        snap = latest_snaps.get(aid)
        for cred in by_account.get(aid, []):
            try:
                api_key = decrypt_secret(cred.encrypted_value, enc_key)
            except Exception:
                logger.warning("proxy pool: skip credential %s (decrypt failed)", cred.id)
                continue
            item: dict = {"credential_id": cred.id, "api_key": api_key}
            if snap is not None:
                item["auto_pct"] = snap.auto_pct
                item["api_pct"] = snap.api_pct
            out.append(item)
    return out


def list_pool_ranking_board(session: Session, *, loan_selection=None) -> dict:
    """Credential Pool Board explain view: ranked + excluded (no secrets)."""
    from pulse.tool_center.burn_rate import explain_lender_selection

    rows, accounts, latest_snaps, loan_counts = _pool_primary_context(session)
    if not rows:
        return {"ranked": [], "excluded": []}

    candidates, excluded_no_snap = _build_pool_lender_candidates(
        accounts,
        latest_snaps,
        loan_counts,
        {c.account_id for c in rows},
        include_no_snap_excluded=True,
    )
    today, now = _pool_scoring_clock(latest_snaps)
    board = explain_lender_selection(
        candidates,
        today,
        loan_selection=loan_selection,
        now=now,
        enforce_loan_cap=False,
    )
    return {
        "ranked": board["ranked"],
        "excluded": excluded_no_snap + board["excluded"],
    }
