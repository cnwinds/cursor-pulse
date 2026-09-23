"""Credential Pool Board — intake ranking for the MITM Credential Pool.

Orchestrates Quota Snapshot Read → LenderCandidate assembly → burn_rate scoring
(enforce_loan_cap=False applies Snapshot Headroom OR). Callers should not
re-implement intake filters.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.tool_center.burn_rate import LenderCandidate
from pulse.util.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

LOAN_CANDIDATE_CACHE_MAX = 512


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
    accounts = {a.id: a for a in session.execute(select(AiAccount).where(AiAccount.id.in_(account_ids))).scalars()}
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
    member_names = member_names_by_id(session, {a.primary_member_id for a in accounts.values() if a.primary_member_id})
    return PoolPrimaryContext(rows, accounts, latest_snaps, loan_counts, bound_at_by_account, member_names)


def _pool_scoring_clock(latest_snaps: dict) -> tuple[date, datetime]:
    """与配额快照 captured_at 对齐的 today/now，避免墙钟与快照数据脱节。"""
    captured: list[datetime] = []
    for snap in latest_snaps.values():
        if snap.captured_at is None:
            continue
        t = snap.captured_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        captured.append(t)
    now = max(captured) if captured else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
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
                        "primary_member_name": (
                            member_names.get(account.primary_member_id) if account.primary_member_id else None
                        ),
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
                    member_names.get(account.primary_member_id) if account.primary_member_id else None
                ),
            )
        )
    return candidates, excluded_no_snap


def loan_candidate_credentials(
    session: Session,
    *,
    loan,
    loan_selection=None,
    ttl_seconds: float = 600.0,
) -> list[str]:
    """借用可游走的候选 primary 凭证 ID（按打分排序，含当前绑定账号）。

    借用 Key（pka_）不再钉死单一账号：Go 在 Pulse 给出的这份白名单内做
    sticky + Switch dwell + 按 Quota Pool 的选择。白名单取自 Credential Pool
    的入池账号，因此必然是 Go 池内已有的凭证。

    结果按 loan_id 缓存 ttl_seconds：Go 对 loan_alias **每个请求**都会调
    authorize，不能每次都重算排名。

    这里**不问 Jev**：本函数在请求路径上，而 CONTEXT.md「Jev Decision」明确
    要求 Jev 不进请求路径。Jev 的主判发生在发放与池刷新，白名单顺序由确定性
    打分给出。
    """
    loan_id = getattr(loan, "id", "") or ""
    if not loan_id:
        return []
    cached = _loan_candidates_get(loan_id, ttl_seconds)
    if cached is not None:
        return cached
    # 同一 loan 的并发 authorize 只算一次：先抢到 per-loan 锁的线程负责计算，
    # 其余线程在锁内二次命中上面的缓存。
    lock = _loan_inflight_lock(loan_id)
    try:
        with lock:
            cached = _loan_candidates_get(loan_id, ttl_seconds)
            if cached is not None:
                return cached
            ordered = _rank_loan_candidates(session, loan, loan_selection=loan_selection)
            _loan_candidates_put(loan_id, ordered, ttl_seconds=ttl_seconds)
            return ordered
    finally:
        _release_inflight_lock(loan_id, lock)


def _rank_loan_candidates(session: Session, loan, *, loan_selection=None) -> list[str]:
    """算一次白名单：入池 primary 账号 → 借用侧硬过滤 → 打分排序 → 凭证 ID。"""
    from pulse.storage.models import AiAccount
    from pulse.tool_center.auto_lender import rank_lenders
    from pulse.tool_center.key_loan_auto import own_cursor_account_ids
    from pulse.tool_center.sync_health import sync_blockers_by_account

    account = session.get(AiAccount, loan.source_account_id)
    team_id = getattr(account, "team_id", None) if account else None
    if not team_id:
        return []

    ctx = _pool_primary_context(session)
    if not ctx.creds:
        return []
    cred_by_account = {cred.account_id: cred.id for cred in ctx.creds}

    candidates, _ = _build_pool_lender_candidates(
        ctx.accounts,
        ctx.latest_snaps,
        ctx.loan_counts,
        set(cred_by_account),
        include_no_snap_excluded=False,
        bound_at_by_account=ctx.bound_at_by_account,
        member_names=ctx.member_names,
    )
    own_accounts = own_cursor_account_ids(session, team_id, loan.borrower_member_id)
    # 与 build_lender_candidates 同一口径：同步不正常的账号不再被新选中游走
    blockers = sync_blockers_by_account(session, [c.account_id for c in candidates])
    candidates = [
        candidate
        for candidate in candidates
        if candidate.account_id not in own_accounts and candidate.account_id not in blockers
    ]

    today, now = _pool_scoring_clock(ctx.latest_snaps)
    board = rank_lenders(
        candidates,
        loan_selection=loan_selection,
        pool="unknown",
        today=today,
        now=now,
        # 借用路径的硬过滤，但不在借人数上限上排除：本笔借用自己就可能占满名额，
        # 否则当前账号会被自己的借用挤出去。
        enforce_loan_cap=True,
        exclude_at_loan_cap=False,
    )

    ordered: list[str] = []
    for row in board["ranked"]:
        cred_id = cred_by_account.get(row["account_id"])
        if cred_id and cred_id not in ordered:
            ordered.append(cred_id)
    # 当前绑定账号必须留在白名单里：否则借用人会瞬间失去正在用的账号。
    # 同步异常也照样保留——它可能正是本笔借用此刻在用的账号，抽掉等于会话中途硬切。
    current = cred_by_account.get(loan.source_account_id)
    if current and current not in ordered:
        ordered.insert(0, current)
    return ordered


_loan_candidates_lock = threading.Lock()
_loan_candidates = TTLCache(LOAN_CANDIDATE_CACHE_MAX)
# 同一 loan 的并发 authorize 只算一次：先抢到 per-loan 锁的线程负责计算。
_loan_candidates_inflight: dict[str, threading.Lock] = {}


def _loan_inflight_lock(loan_id: str) -> threading.Lock:
    """取（或创建）该 loan 的计算锁。"""
    with _loan_candidates_lock:
        lock = _loan_candidates_inflight.get(loan_id)
        if lock is None:
            lock = threading.Lock()
            _loan_candidates_inflight[loan_id] = lock
        return lock


def _release_inflight_lock(loan_id: str, lock: threading.Lock) -> None:
    """回收计算锁引用；仍有等待者持有时留给它自己回收。"""
    with _loan_candidates_lock:
        if _loan_candidates_inflight.get(loan_id) is lock and not lock.locked():
            _loan_candidates_inflight.pop(loan_id, None)


def _loan_candidates_get(loan_id: str, ttl_seconds: float) -> list[str] | None:
    """读白名单缓存（TTL 见 :mod:`pulse.util.ttl_cache`）。"""
    return _loan_candidates.get(loan_id, ttl_seconds)


def _loan_candidates_put(loan_id: str, credential_ids: list[str], *, ttl_seconds: float = 0.0) -> None:
    """写白名单缓存；ttl 只用于超上限时清理过期项。"""
    _loan_candidates.put(loan_id, credential_ids, ttl_seconds=ttl_seconds)


def reset_loan_candidate_cache() -> None:
    """清空白名单缓存（测试 / 手动改绑后立即生效）。"""
    _loan_candidates.clear()


def forget_loan_candidate_cache(loan_id: str) -> None:
    """丢弃单笔借用的白名单缓存：改绑出借账号后不必等 TTL。"""
    _loan_candidates.pop(loan_id)


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


def ranked_pool_credential_pairs(
    session: Session,
    *,
    loan_selection=None,
    jev=None,
    quota_pool=None,
) -> list[tuple[str, str]]:
    """账号池打分顺序的 ``(primary credential_id, account_id)``，不含密钥明文。

    与打分表、``list_pool_credentials`` 同一套 ``rank_lenders``（含 Jev 缓存）。
    同时在线选座用它，避免为了顺序去解密 Cursor Key。
    """
    from pulse.tool_center.auto_lender import rank_lenders

    ctx = _pool_primary_context(session)
    if not ctx.creds:
        return []
    candidates, _excluded = _build_pool_lender_candidates(
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
    by_account = {cred.account_id: cred.id for cred in ctx.creds}
    pairs: list[tuple[str, str]] = []
    for row in board["ranked"]:
        cred_id = by_account.get(row["account_id"])
        if cred_id:
            pairs.append((cred_id, row["account_id"]))
    return pairs


def list_pool_ranking_board(
    session: Session,
    *,
    loan_selection=None,
    jev=None,
    quota_pool=None,
    jev_bypass_cache: bool = False,
) -> dict:
    """Credential Pool Board explain view: ranked + excluded + decision (no secrets)."""
    from pulse.tool_center.auto_lender import rank_lenders

    ctx = _pool_primary_context(session)
    if not ctx.creds:
        selection = loan_selection
        ttl = float(getattr(selection, "concurrent_ttl_seconds", 180) or 180)
        max_seats = int(getattr(selection, "max_concurrent_users", 3) or 0)
        return {
            "ranked": [],
            "excluded": [],
            "decision": {"picked_by": "algorithm", "fallback_reason": "no_credentials"},
            "seat_snapshot": {
                "max_concurrent_users": max_seats,
                "ttl_seconds": int(ttl),
            },
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
        jev_bypass_cache=jev_bypass_cache,
    )
    from pulse.proxy.occupancy import get_occupancy

    selection = loan_selection
    ttl = float(getattr(selection, "concurrent_ttl_seconds", 180) or 180)
    max_seats = int(getattr(selection, "max_concurrent_users", 3) or 0)
    seat_counts = get_occupancy().count_by_account(ttl_seconds=ttl)

    def _with_proxy_seats(row: dict) -> dict:
        aid = row.get("account_id") or ""
        out = dict(row)
        out["proxy_active_seats"] = int(seat_counts.get(aid, 0))
        return out

    ranked = [_with_proxy_seats(r) for r in board["ranked"]]
    excluded = [_with_proxy_seats(r) for r in excluded_no_snap + board["excluded"]]
    return {
        "ranked": ranked,
        "excluded": excluded,
        "decision": board["decision"],
        "seat_snapshot": {
            "max_concurrent_users": max_seats,
            "ttl_seconds": int(ttl),
        },
    }
