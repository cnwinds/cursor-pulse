"""把授权结果落成同时在线座位，并给出 Go 应使用的凭证。

授权本身仍由 ``authorize_status`` 决定。这里失败时原样返回，Go 按本地选号继续
（fail-open）。显式给出空分配时 Go 不再往已满账号上加人（fail-closed）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy.occupancy import SeatChoice, get_occupancy, seat_holder_id
from pulse.storage.models import AiAccountCredential, KeyLoan, Member, ProxyKey

logger = logging.getLogger(__name__)


QUOTA_POOLS = frozenset({"auto", "api"})


def selection_for_pulse_key(session: Session, config, plaintext: str):
    """授权打分用的选号参数：按这把 Key 的成员所在团队覆盖。"""
    return _runtime_for_member(session, config, _member_id_for_plaintext(session, plaintext)).tool_center.loan_selection


def _member_id_for_plaintext(session: Session, plaintext: str) -> str | None:
    from pulse.proxy import key_crud
    from pulse.proxy.keys import hash_proxy_key
    from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS

    plaintext = (plaintext or "").strip()
    if plaintext.startswith("pka_"):
        loan = session.scalar(
            select(KeyLoan).where(
                KeyLoan.alias_key_hash == hash_proxy_key(plaintext),
                KeyLoan.delivery_mode == DELIVERY_PROXY_ALIAS,
            )
        )
        return loan.borrower_member_id if loan is not None else None
    if plaintext.startswith("pk_"):
        key = key_crud.find_key_by_plaintext(session, plaintext)
        return key.member_id if key is not None else None
    if plaintext.startswith("cr"):
        cred = session.scalar(
            select(AiAccountCredential).where(
                AiAccountCredential.key_hash == hash_proxy_key(plaintext),
                AiAccountCredential.key_role == "loan",
            )
        )
        if cred is None:
            return None
        loan = session.scalar(
            select(KeyLoan).where(
                KeyLoan.credential_id == cred.id,
                KeyLoan.status == "active",
            )
        )
        return loan.borrower_member_id if loan is not None else None
    return None


def apply_seat(
    session: Session,
    auth: dict,
    *,
    current_credential_id: str | None,
    release_current: bool,
    config,
    quota_pool: str = "auto",
    held_credential_ids: list[str] | None = None,
    jev=None,
    now: float | None = None,
) -> dict:
    """``status=ok`` 之后调用。座位异常不改变授权结论。

    ``quota_pool`` 是 Go 这个会话槽所属的桶（auto/api），只按该桶的打分表顺序、
    且只分配该桶还有余量的账号。``held_credential_ids`` 是同一会话另一个桶正在用的凭证。
    """
    if auth.get("status") != "ok":
        return auth
    current = (current_credential_id or "").strip() or None
    held = [cid.strip() for cid in (held_credential_ids or []) if cid and cid.strip()]
    try:
        extra = _advise(
            session,
            auth,
            current_credential_id=current,
            release_current=bool(release_current),
            config=config,
            quota_pool=quota_pool if quota_pool in QUOTA_POOLS else "auto",
            held_credential_ids=held,
            jev=jev,
            now=now,
        )
    except Exception:
        logger.warning("seat assignment failed; authorize continues without advice", exc_info=True)
        return auth
    return {**auth, **extra}


def _advise(
    session: Session,
    auth: dict,
    *,
    current_credential_id: str | None,
    release_current: bool,
    config,
    quota_pool: str,
    held_credential_ids: list[str],
    jev,
    now: float | None,
) -> dict:
    member_id = _member_id(session, auth)
    runtime = _runtime_for_member(session, config, member_id)
    selection = runtime.tool_center.loan_selection
    holder = seat_holder_id(
        member_id=member_id,
        loan_id=auth.get("loan_id"),
        proxy_key_id=auth.get("proxy_key_id"),
    )
    mode = auth.get("mode") or ""
    allowlist = [cid for cid in (auth.get("credential_ids") or []) if cid]
    pinned = mode == "loan_passthrough" or (mode == "loan_alias" and not allowlist)
    pinned_credential_id = auth.get("credential_id") if pinned else None

    if pinned:
        ranked: list[tuple[str, str]] = []
    elif mode == "loan_alias":
        accounts = _accounts_for(session, allowlist)
        ranked = [(cid, accounts[cid]) for cid in allowlist if cid in accounts]
        ranked = _in_pool_order(ranked, _pool_ranked(session, selection, jev, runtime, quota_pool))
        ranked = _with_pool_headroom(session, ranked, quota_pool)
    else:
        ranked = _pool_ranked(session, selection, jev, runtime, quota_pool)

    known_ids = [cid for cid, _ in ranked] + list(held_credential_ids)
    if current_credential_id:
        known_ids.append(current_credential_id)
    if pinned_credential_id:
        known_ids.append(pinned_credential_id)
    accounts = _accounts_for(session, known_ids)
    for cid, account_id in ranked:
        accounts.setdefault(cid, account_id)

    choice = get_occupancy().choose(
        holder_id=holder,
        ranked=ranked,
        account_by_credential=accounts,
        current_credential_id=current_credential_id,
        release_current=release_current,
        pinned=pinned,
        pinned_credential_id=pinned_credential_id,
        held_credential_ids=held_credential_ids,
        max_concurrent=int(selection.max_concurrent_users),
        ttl_seconds=float(selection.concurrent_ttl_seconds),
        now=now,
    )
    return _advice_fields(
        choice,
        pinned=pinned,
        release_current=release_current,
        max_concurrent=int(selection.max_concurrent_users),
    )


def _advice_fields(
    choice: SeatChoice,
    *,
    pinned: bool,
    release_current: bool,
    max_concurrent: int,
) -> dict:
    """没有候选、也不是正在离开时，不要把空池说成人满。

    正在离开且没有可去的账号，仍要 advised，这样 Go 不会把刚释放的凭证再选回来。
    """
    advised = include_seat_advice(choice, pinned=pinned, release_current=release_current)
    return {
        "assigned_credential_id": choice.assigned_credential_id,
        "blocked_credential_ids": choice.blocked_credential_ids,
        "max_concurrent_users": max_concurrent,
        "seat_advised": advised,
    }


def include_seat_advice(choice: SeatChoice, *, pinned: bool, release_current: bool) -> bool:
    if pinned or release_current:
        return True
    if choice.assigned_credential_id or choice.blocked_credential_ids:
        return True
    return False


def _member_id(session: Session, auth: dict) -> str | None:
    loan_id = auth.get("loan_id")
    if loan_id:
        loan = session.get(KeyLoan, loan_id)
        if loan is not None and loan.borrower_member_id:
            return loan.borrower_member_id
    proxy_key_id = auth.get("proxy_key_id")
    if proxy_key_id:
        key = session.get(ProxyKey, proxy_key_id)
        if key is not None and key.member_id:
            return key.member_id
    return None


def _runtime_for_member(session: Session, config, member_id: str | None):
    """选号参数和 Jev 共用的生效配置：成员所在团队覆盖，否则与打分表同一份租户配置。"""
    from pulse.settings.team_store import effective_config, effective_config_for_saved_tenant

    member = session.get(Member, member_id) if member_id else None
    team_id = getattr(member, "team_id", None) if member is not None else None
    if team_id:
        return effective_config(config, session, team_id)
    return effective_config_for_saved_tenant(session, config)


def _accounts_for(session: Session, credential_ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(cid for cid in credential_ids if cid))
    if not ids:
        return {}
    rows = session.execute(
        select(AiAccountCredential.id, AiAccountCredential.account_id).where(AiAccountCredential.id.in_(ids))
    ).all()
    return {row[0]: row[1] for row in rows if row[1]}


def _in_pool_order(ranked: list[tuple[str, str]], pool_order: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """白名单按该桶打分表排序；表里没有的（多半没有快照）按原顺序排在后面。"""
    position = {cid: i for i, (cid, _) in enumerate(pool_order)}
    listed = sorted((pair for pair in ranked if pair[0] in position), key=lambda pair: position[pair[0]])
    return listed + [pair for pair in ranked if pair[0] not in position]


def _with_pool_headroom(session: Session, ranked: list[tuple[str, str]], quota_pool: str) -> list[tuple[str, str]]:
    """去掉该桶已无 Snapshot Headroom 的账号；没有快照的保留，交给 Go 判断。"""
    from pulse.tool_center.quota_reads import latest_snapshots_for_accounts
    from pulse.tool_center.snapshot_headroom import snapshot_quota_ok_for_pool

    snaps = latest_snapshots_for_accounts(session, list({account_id for _, account_id in ranked}))
    out: list[tuple[str, str]] = []
    for cid, account_id in ranked:
        snap = snaps.get(account_id)
        if snap is not None and not snapshot_quota_ok_for_pool(
            quota_pool, auto_pct=snap.auto_pct, api_pct=snap.api_pct
        ):
            continue
        out.append((cid, account_id))
    return out


def _pool_ranked(session: Session, loan_selection, jev, runtime, quota_pool: str) -> list[tuple[str, str]]:
    from pulse.llm.jev import build_jev_client
    from pulse.proxy.pool_board import ranked_pool_credential_pairs

    # 与打分表同一 Jev 缓存；白名单本身（loan_candidate_credentials）仍不问 Jev。
    if jev is None and runtime is not None:
        jev = build_jev_client(runtime)
    return ranked_pool_credential_pairs(session, loan_selection=loan_selection, jev=jev, quota_pool=quota_pool)
