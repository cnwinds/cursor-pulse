"""Credential Pool Board — intake ranking for the MITM Credential Pool.

Orchestrates Quota Snapshot Read → LenderCandidate assembly → burn_rate scoring
(enforce_loan_cap=False applies Snapshot Headroom OR). Callers should not
re-implement intake filters.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.tool_center.burn_rate import LenderCandidate

logger = logging.getLogger(__name__)


class PoolPrimaryContext(NamedTuple):
    """Credential Pool 入池 primary 的读取结果。

    注意与 Quota Pool（auto/api 计费桶）区分：本结构描述的是凭证集合。
    """

    creds: list
    accounts: dict
    latest_snaps: dict
    loan_counts: dict
    bound_at_by_account: dict
    member_names: dict


def _pool_primary_context(session: Session) -> PoolPrimaryContext:
    """读取入池 primary 凭证及其打分所需上下文。"""
    from pulse.storage.models import (
        AiAccount,
        AiAccountCredential,
        AiVendor,
        KeyLoan,
    )
    from pulse.tool_center.key_loan_lender import (
        last_bound_at_by_account,
        member_names_by_id,
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
        return PoolPrimaryContext([], {}, {}, {}, {}, {})

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
    bound_at_by_account = last_bound_at_by_account(session, account_ids)
    member_names = member_names_by_id(
        session, {a.primary_member_id for a in accounts.values() if a.primary_member_id}
    )
    return PoolPrimaryContext(
        rows, accounts, latest_snaps, loan_counts, bound_at_by_account, member_names
    )


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
    bound_at_by_account: dict | None = None,
    member_names: dict | None = None,
) -> tuple[list[LenderCandidate], list[dict]]:
    """Assemble LenderCandidates; optionally collect no_snapshot exclusions.

    Snapshot Headroom is applied by burn_rate (enforce_loan_cap=False). Accounts
    that already fail ``snapshot_has_any_pool_headroom`` are still passed through
    so explain_lender_selection can surface the reason.

    bound_at / primary_member_name 必须与借用路径一致，否则驻留降权恒为 1.0，
    且 Jev 候选描述与 owner 列会退化成 unassigned。
    """
    candidates: list[LenderCandidate] = []
    excluded_no_snap: list[dict] = []
    bound_at_by_account = bound_at_by_account or {}
    member_names = member_names or {}
    for aid in sorted(account_ids):
        account = accounts.get(aid)
        if not account:
            continue
        snap = latest_snaps.get(aid)
        active_loans = loan_counts.get(aid, 0)
        if not snap:
            if include_no_snap_excluded:
                adjust = account.proxy_score_adjust
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
                        "auto_pct": None,
                        "api_pct": None,
                        "score_adjust": None if adjust is None else round(adjust, 4),
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
                score_adjust=account.proxy_score_adjust,
                reserve_pct=account.proxy_reserve_pct,
                bound_at=bound_at_by_account.get(aid),
                primary_member_name=(
                    member_names.get(account.primary_member_id)
                    if account.primary_member_id
                    else None
                ),
            )
        )
    return candidates, excluded_no_snap


def list_pool_credentials(
    session: Session,
    *,
    encryption_key: str,
    loan_selection=None,
    jev=None,
    quota_pool=None,
) -> list[dict]:
    """Credential Pool Board → decryptable primary credentials (ranked).

    Auto Lender（Jev + 算法保底）决定顺序；Go 代理按该顺序做 sticky 轮转。
    ``quota_pool`` 为 Quota Pool（auto/api）过滤，None 表示不限定——代理入池走
    CONTEXT.md 的 Credential Pool Intake 规则（任一桶有余量即可入池），
    请求时再由 Go 按具体桶过滤，因此默认保持 None。
    """
    from pulse.ingestion.crypto import decrypt_secret
    from pulse.storage.models import AiAccountCredential
    from pulse.tool_center.auto_lender import rank_lenders

    ctx = _pool_primary_context(session)
    if not ctx.creds:
        return []

    candidates, _ = _build_pool_lender_candidates(
        ctx.accounts,
        ctx.latest_snaps,
        ctx.loan_counts,
        {c.account_id for c in ctx.creds},
        include_no_snap_excluded=False,
        bound_at_by_account=ctx.bound_at_by_account,
        member_names=ctx.member_names,
    )
    today, now = _pool_scoring_clock(ctx.latest_snaps)
    board = rank_lenders(
        candidates,
        loan_selection=loan_selection,
        pool=quota_pool,
        today=today,
        now=now,
        enforce_loan_cap=False,
        jev=jev,
    )
    ranked_ids = [item["account_id"] for item in board["ranked"]]
    allowed = set(ranked_ids)

    by_account: dict[str, list[AiAccountCredential]] = {}
    for cred in ctx.creds:
        if cred.account_id not in allowed:
            continue
        by_account.setdefault(cred.account_id, []).append(cred)

    enc_key = (encryption_key or "").strip()
    out: list[dict] = []
    for aid in ranked_ids:
        snap = ctx.latest_snaps.get(aid)
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


def list_pool_ranking_board(
    session: Session, *, loan_selection=None, jev=None, quota_pool=None
) -> dict:
    """Credential Pool Board explain view: ranked + excluded + decision (no secrets)."""
    from pulse.tool_center.auto_lender import rank_lenders

    ctx = _pool_primary_context(session)
    if not ctx.creds:
        return {
            "ranked": [],
            "excluded": [],
            "decision": {"picked_by": "algorithm", "fallback_reason": "no_credentials"},
        }

    candidates, excluded_no_snap = _build_pool_lender_candidates(
        ctx.accounts,
        ctx.latest_snaps,
        ctx.loan_counts,
        {c.account_id for c in ctx.creds},
        include_no_snap_excluded=True,
        bound_at_by_account=ctx.bound_at_by_account,
        member_names=ctx.member_names,
    )
    today, now = _pool_scoring_clock(ctx.latest_snaps)
    board = rank_lenders(
        candidates,
        loan_selection=loan_selection,
        pool=quota_pool,
        today=today,
        now=now,
        enforce_loan_cap=False,
        jev=jev,
    )
    return {
        "ranked": board["ranked"],
        "excluded": excluded_no_snap + board["excluded"],
        "decision": board["decision"],
    }
