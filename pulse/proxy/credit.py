"""Member credit wallet — sole module allowed to mutate balances."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulse.storage.models import CreditAccount, CreditTransaction, ProxyKeyUsage

_SKIP_POOL = "skip"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _read_balance(session: Session, member_id: str) -> int:
    value = session.scalar(select(CreditAccount.balance_cents).where(CreditAccount.member_id == member_id))
    return int(value or 0)


def _ensure_account(session: Session, member_id: str) -> None:
    existing = session.get(CreditAccount, member_id)
    if existing is not None:
        return
    try:
        with session.begin_nested():
            session.add(CreditAccount(member_id=member_id, balance_cents=0))
            session.flush()
    except IntegrityError:
        pass


def _apply_delta(session: Session, member_id: str, delta: int) -> int:
    _ensure_account(session, member_id)
    now = _utcnow()
    session.execute(
        text(
            "UPDATE credit_accounts SET balance_cents = balance_cents + :delta, updated_at = :now "
            "WHERE member_id = :mid"
        ),
        {"delta": delta, "now": now, "mid": member_id},
    )
    row = session.get(CreditAccount, member_id)
    if row is not None:
        session.expire(row, ["balance_cents", "updated_at"])
    return _read_balance(session, member_id)


def balance_cents(session: Session, member_id: str) -> int:
    return _read_balance(session, member_id)


def grant(
    session: Session,
    member_id: str,
    amount_cents: int,
    *,
    actor_member_id: str | None = None,
    note: str | None = None,
) -> CreditTransaction:
    if amount_cents <= 0:
        raise ValueError("充值金额须为正数")
    balance = _apply_delta(session, member_id, amount_cents)
    txn = CreditTransaction(
        member_id=member_id,
        kind="grant",
        amount_cents=amount_cents,
        balance_after_cents=balance,
        actor_member_id=actor_member_id,
        note=note,
    )
    session.add(txn)
    session.flush()
    return txn


def adjust(
    session: Session,
    member_id: str,
    amount_cents: int,
    *,
    actor_member_id: str | None = None,
    note: str,
) -> CreditTransaction:
    if amount_cents == 0:
        raise ValueError("调整金额不能为零")
    if not (note or "").strip():
        raise ValueError("调整须填写备注")
    balance = _apply_delta(session, member_id, amount_cents)
    txn = CreditTransaction(
        member_id=member_id,
        kind="adjust",
        amount_cents=amount_cents,
        balance_after_cents=balance,
        actor_member_id=actor_member_id,
        note=note.strip(),
    )
    session.add(txn)
    session.flush()
    return txn


def refund_charge(
    session: Session,
    txn_id: int,
    *,
    actor_member_id: str | None = None,
    note: str | None = None,
) -> CreditTransaction:
    original = session.get(CreditTransaction, txn_id)
    if original is None or original.kind != "charge":
        raise ValueError("只能退还扣费流水")
    existing = session.scalar(select(CreditTransaction.id).where(CreditTransaction.ref_transaction_id == txn_id))
    if existing is not None:
        raise ValueError("该扣费已退还")
    refund_amount = -int(original.amount_cents)
    balance = _apply_delta(session, original.member_id, refund_amount)
    txn = CreditTransaction(
        member_id=original.member_id,
        kind="refund",
        amount_cents=refund_amount,
        balance_after_cents=balance,
        ref_transaction_id=txn_id,
        actor_member_id=actor_member_id,
        note=note,
    )
    session.add(txn)
    session.flush()
    return txn


def charge_usage(session: Session, member_id: str, usage_row: ProxyKeyUsage) -> CreditTransaction | None:
    pool = (usage_row.usage_cap_pool or "").strip()
    if pool == _SKIP_POOL:
        return None
    cost = int(usage_row.cost_cents or 0)
    if cost == 0:
        return None
    existing = session.scalar(select(CreditTransaction).where(CreditTransaction.usage_id == usage_row.id))
    if existing is not None:
        return existing
    amount = -cost
    balance = _apply_delta(session, member_id, amount)
    txn = CreditTransaction(
        member_id=member_id,
        kind="charge",
        amount_cents=amount,
        balance_after_cents=balance,
        usage_id=usage_row.id,
    )
    session.add(txn)
    session.flush()
    return txn


def reconcile(session: Session) -> list[dict]:
    """比对 ``balance_cents`` 与流水合计；只报告，不修正。"""
    mismatches: list[dict] = []
    accounts = session.scalars(select(CreditAccount)).all()
    for acct in accounts:
        summed = session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_cents), 0)).where(
                CreditTransaction.member_id == acct.member_id
            )
        )
        summed = int(summed or 0)
        if summed != int(acct.balance_cents):
            mismatches.append(
                {
                    "member_id": acct.member_id,
                    "balance_cents": int(acct.balance_cents),
                    "sum_amount_cents": summed,
                    "delta": int(acct.balance_cents) - summed,
                }
            )
    txn_members = session.scalars(select(CreditTransaction.member_id).distinct()).all()
    for member_id in txn_members:
        if session.get(CreditAccount, member_id) is None:
            summed = session.scalar(
                select(func.coalesce(func.sum(CreditTransaction.amount_cents), 0)).where(
                    CreditTransaction.member_id == member_id
                )
            )
            mismatches.append(
                {
                    "member_id": member_id,
                    "balance_cents": 0,
                    "sum_amount_cents": int(summed or 0),
                    "delta": -int(summed or 0),
                }
            )
    return mismatches
