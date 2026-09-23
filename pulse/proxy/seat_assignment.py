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


def selection_for_pulse_key(session: Session, config, plaintext: str):
    """授权打分用的选号参数：按这把 Key 的成员所在团队覆盖。"""
    return _selection_for_member(session, config, _member_id_for_plaintext(session, plaintext))


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
    jev=None,
    now: float | None = None,
) -> dict:
    """``status=ok`` 之后调用。座位异常不改变授权结论。"""
    if auth.get("status") != "ok":
        return auth
    try:
        extra = _advise(
            session,
            auth,
            current_credential_id=(current_credential_id or "").strip() or None,
            release_current=bool(release_current),
            config=config,
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
    jev,
    now: float | None,
) -> dict:
    member_id = _member_id(session, auth)
    selection = _selection_for_member(session, config, member_id)
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
    else:
        ranked = _pool_ranked(session, selection, jev, config)

    known_ids = [cid for cid, _ in ranked]
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


def _selection_for_member(session: Session, config, member_id: str | None):
    base = config.tool_center.loan_selection
    if not member_id:
        return base
    member = session.get(Member, member_id)
    team_id = getattr(member, "team_id", None) if member is not None else None
    if not team_id:
        return base
    from pulse.settings.team_store import effective_config

    return effective_config(config, session, team_id).tool_center.loan_selection


def _accounts_for(session: Session, credential_ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(cid for cid in credential_ids if cid))
    if not ids:
        return {}
    rows = session.execute(
        select(AiAccountCredential.id, AiAccountCredential.account_id).where(AiAccountCredential.id.in_(ids))
    ).all()
    return {row[0]: row[1] for row in rows if row[1]}


def _pool_ranked(session: Session, loan_selection, jev, config) -> list[tuple[str, str]]:
    from pulse.llm.jev import build_jev_client
    from pulse.proxy.pool_board import ranked_pool_credential_pairs

    # 只在账号池顺序上用 Jev（与打分表同一缓存）。借用白名单仍不问 Jev。
    if jev is None and config is not None:
        jev = build_jev_client(config)
    return ranked_pool_credential_pairs(session, loan_selection=loan_selection, jev=jev)
