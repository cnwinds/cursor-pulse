"""Membership / credit API serialization (shared with loan list presentation)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.proxy.credit import balance_cents
from pulse.proxy.key_crud import cents_to_usd
from pulse.proxy.membership import active_membership, effective_policy
from pulse.proxy.spend_policy import SpendRule, parse_spend_rules, rule_snapshots, rules_from_json
from pulse.storage.models import (
    CreditAccount,
    CreditTransaction,
    KeyLoan,
    Member,
    Membership,
    MembershipPlan,
    ProxyKey,
    ProxyKeyUsage,
)
from pulse.util.datetime_fmt import ensure_aware, serialize_datetime

_BEIJING = ZoneInfo("Asia/Shanghai")
_USAGE_CAPS_MIGRATED_MSG = "用量限制已迁移到会员，请在「会员」中设置"


def usage_caps_migrated_message() -> str:
    return _USAGE_CAPS_MIGRATED_MSG


def parse_usd_amount(
    value: Any,
    *,
    allow_zero: bool = False,
    allow_negative: bool = False,
) -> int:
    if value is None:
        raise ValueError("金额不能为空")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("金额格式无效") from exc
    if not amount.is_finite():
        raise ValueError("金额格式无效")
    if amount.as_tuple().exponent < -2:
        raise ValueError("金额最多两位小数")
    cents = int(amount * 100)
    if not allow_negative and cents < 0:
        raise ValueError("金额须为正数")
    if not allow_zero and cents == 0:
        raise ValueError("金额不能为零")
    if allow_negative and not allow_zero and cents == 0:
        raise ValueError("调整金额不能为零")
    return cents


def parse_opening_credit_usd(value: Any) -> int | None:
    """Map null or non-positive USD to no opening credit; negative values raise."""
    if value is None:
        return None
    cents = parse_usd_amount(value, allow_zero=True)
    if cents <= 0:
        return None
    return cents


def rule_dict_out(rule: SpendRule) -> dict:
    return {
        "period": rule.period,
        "pool": rule.pool,
        "cost_usd": cents_to_usd(rule.limit_cents),
        "limit_cents": rule.limit_cents,
    }


def rules_json_out(raw: list | None) -> list[dict]:
    return [rule_dict_out(rule) for rule in rules_from_json(raw)]


def plan_out(session: Session, plan: MembershipPlan) -> dict:
    member_count = session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.plan_id == plan.id,
            Membership.status == "active",
        )
    )
    return {
        "id": plan.id,
        "name": plan.name,
        "rules": rules_json_out(plan.rules),
        "credit_mode": plan.credit_mode,
        "opening_credit_cents": plan.opening_credit_cents,
        "status": plan.status,
        "member_count": int(member_count or 0),
        "created_at": serialize_datetime(plan.created_at),
        "updated_at": serialize_datetime(plan.updated_at),
    }


def membership_out(
    session: Session,
    membership: Membership,
    *,
    now: datetime | None = None,
    plan: MembershipPlan | None = None,
) -> dict:
    policy = effective_policy(session, membership)
    if plan is None and membership.plan_id:
        plan = session.get(MembershipPlan, membership.plan_id)
    override_raw = membership.rules_override
    return {
        "id": membership.id,
        "plan_id": membership.plan_id,
        "plan_name": plan.name if plan else None,
        "plan_status": plan.status if plan else None,
        "rules_override": rules_json_out(override_raw) if override_raw is not None else None,
        "credit_mode_override": membership.credit_mode_override,
        "credit_mode": policy.credit_mode,
        "effective_rules": rule_snapshots(
            session,
            policy.rules,
            member_id=membership.member_id,
            now=now,
        ),
        "status": membership.status,
        "created_at": serialize_datetime(membership.created_at),
        "updated_at": serialize_datetime(membership.updated_at),
    }


def preload_member_membership_context(session: Session, member_ids: list[str]) -> dict:
    if not member_ids:
        return {"memberships": {}, "plans": {}, "accounts": {}}
    memberships = {
        row.member_id: row
        for row in session.scalars(
            select(Membership).where(
                Membership.member_id.in_(member_ids),
                Membership.status == "active",
            )
        ).all()
    }
    plan_ids = {m.plan_id for m in memberships.values() if m.plan_id}
    plans = {
        p.id: p
        for p in (
            session.scalars(select(MembershipPlan).where(MembershipPlan.id.in_(plan_ids))).all() if plan_ids else []
        )
    }
    accounts = {
        row.member_id: row
        for row in session.scalars(select(CreditAccount).where(CreditAccount.member_id.in_(member_ids))).all()
    }
    return {"memberships": memberships, "plans": plans, "accounts": accounts}


def member_membership_row(
    session: Session,
    member: Member,
    *,
    membership: Membership | None = None,
    now: datetime | None = None,
    plans: dict[str, MembershipPlan] | None = None,
    accounts: dict[str, CreditAccount] | None = None,
    membership_provided: bool = False,
) -> dict:
    if not membership_provided:
        if membership is None:
            membership = active_membership(session, member.id)
    if accounts is not None:
        account = accounts.get(member.id)
        has_account = account is not None
        balance = int(account.balance_cents) if account is not None else 0
    else:
        has_account = session.get(CreditAccount, member.id) is not None
        balance = balance_cents(session, member.id)
    plan = None
    if membership is not None and membership.plan_id and plans is not None:
        plan = plans.get(membership.plan_id)
    return {
        "member_id": member.id,
        "member_name": member.display_name,
        "membership": membership_out(session, membership, now=now, plan=plan) if membership else None,
        "balance_cents": balance,
        "has_account": has_account,
    }


def _usage_source_label(
    session: Session, usage: ProxyKeyUsage, keys: dict[str, ProxyKey], loans: dict[str, KeyLoan]
) -> dict:
    if usage.proxy_key_id and usage.proxy_key_id in keys:
        key = keys[usage.proxy_key_id]
        label = (key.name or "").strip() or key.key_hint
        return {"kind": "proxy_key", "label": label}
    if usage.loan_id and usage.loan_id in loans:
        loan = loans[usage.loan_id]
        hint = (loan.alias_key_hint or "").strip() or "借用"
        return {"kind": "loan", "label": f"借用 · {hint}"}
    return {"kind": "proxy_key", "label": "未知来源"}


def _usage_out(
    usage: ProxyKeyUsage,
    *,
    source: dict,
) -> dict:
    pool = (usage.usage_cap_pool or "").strip()
    if pool == "skip":
        pool_out = None
    elif pool in ("auto", "api"):
        pool_out = pool
    else:
        pool_out = None
    client = (usage.client or "").strip()
    client_out = client if client in ("cli", "ide") else None
    return {
        "usage_id": usage.id,
        "ts": serialize_datetime(usage.ts),
        "model": usage.model,
        "pool": pool_out,
        "client": client_out,
        "source": source,
        "tokens": {
            "input": int(usage.tokens_input or 0),
            "output": int(usage.tokens_output or 0),
            "cache_read": int(usage.tokens_cache_read or 0),
            "cache_write": int(usage.tokens_cache_write or 0),
            "reasoning": int(usage.tokens_reasoning or 0),
            "total": int(usage.total_tokens or 0),
        },
    }


def _load_statement_context(
    session: Session,
    txns: list[CreditTransaction],
) -> tuple[
    dict[str, ProxyKeyUsage],
    dict[str, ProxyKey],
    dict[str, KeyLoan],
    dict[int, int],
    dict[str, Member],
]:
    usage_ids = [t.usage_id for t in txns if t.usage_id]
    usages: dict[str, ProxyKeyUsage] = {}
    if usage_ids:
        usages = {row.id: row for row in session.scalars(select(ProxyKeyUsage).where(ProxyKeyUsage.id.in_(usage_ids)))}

    key_ids = {u.proxy_key_id for u in usages.values() if u.proxy_key_id}
    loan_ids = {u.loan_id for u in usages.values() if u.loan_id}
    keys = {
        row.id: row
        for row in (session.scalars(select(ProxyKey).where(ProxyKey.id.in_(key_ids))).all() if key_ids else [])
    }
    loans = {
        row.id: row
        for row in (session.scalars(select(KeyLoan).where(KeyLoan.id.in_(loan_ids))).all() if loan_ids else [])
    }

    charge_ids = [t.id for t in txns if t.kind == "charge"]
    refund_by_charge: dict[int, int] = {}
    if charge_ids:
        for refund_id, ref_id in session.execute(
            select(CreditTransaction.id, CreditTransaction.ref_transaction_id).where(
                CreditTransaction.ref_transaction_id.in_(charge_ids)
            )
        ).all():
            if ref_id is not None:
                refund_by_charge[int(ref_id)] = int(refund_id)

    actor_ids = {t.actor_member_id for t in txns if t.actor_member_id}
    actors = {
        row.id: row
        for row in (session.scalars(select(Member).where(Member.id.in_(actor_ids))).all() if actor_ids else [])
    }
    return usages, keys, loans, refund_by_charge, actors


def transaction_out(
    session: Session,
    txn: CreditTransaction,
    *,
    usages: dict[str, ProxyKeyUsage] | None = None,
    keys: dict[str, ProxyKey] | None = None,
    loans: dict[str, KeyLoan] | None = None,
    refund_by_charge: dict[int, int] | None = None,
    actors: dict[str, Member] | None = None,
) -> dict:
    if usages is None:
        usages, keys, loans, refund_by_charge, actors = _load_statement_context(session, [txn])
    keys = keys or {}
    loans = loans or {}
    refund_by_charge = refund_by_charge or {}
    actors = actors or {}

    usage_row = None
    if txn.usage_id and txn.usage_id in usages:
        usage = usages[txn.usage_id]
        source = _usage_source_label(session, usage, keys, loans)
        usage_row = _usage_out(usage, source=source)

    actor = actors.get(txn.actor_member_id) if txn.actor_member_id else None
    out = {
        "id": txn.id,
        "created_at": serialize_datetime(txn.created_at),
        "kind": txn.kind,
        "amount_cents": int(txn.amount_cents),
        "balance_after_cents": int(txn.balance_after_cents),
        "note": txn.note,
        "actor_name": actor.display_name if actor else None,
        "ref_transaction_id": txn.ref_transaction_id,
        "refunded_by_transaction_id": refund_by_charge.get(txn.id) if txn.kind == "charge" else None,
        "usage": usage_row,
    }
    return out


def transactions_out(session: Session, txns: list[CreditTransaction]) -> list[dict]:
    if not txns:
        return []
    usages, keys, loans, refund_by_charge, actors = _load_statement_context(session, txns)
    return [
        transaction_out(
            session,
            txn,
            usages=usages,
            keys=keys,
            loans=loans,
            refund_by_charge=refund_by_charge,
            actors=actors,
        )
        for txn in txns
    ]


def _parse_range(from_ts: datetime | None, to_ts: datetime | None) -> tuple[datetime | None, datetime | None]:
    start = ensure_aware(from_ts) if from_ts else None
    end = ensure_aware(to_ts) if to_ts else None
    return start, end


def query_transactions(
    session: Session,
    member_id: str,
    *,
    cursor: int | None = None,
    limit: int = 50,
    kind: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> tuple[list[CreditTransaction], int | None]:
    start, end = _parse_range(from_ts, to_ts)
    q = select(CreditTransaction).where(CreditTransaction.member_id == member_id)
    if cursor is not None:
        q = q.where(CreditTransaction.id < cursor)
    if kind:
        q = q.where(CreditTransaction.kind == kind)
    if start is not None:
        q = q.where(CreditTransaction.created_at >= start)
    if end is not None:
        q = q.where(CreditTransaction.created_at < end)
    q = q.order_by(CreditTransaction.id.desc()).limit(limit + 1)
    rows = list(session.scalars(q).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = rows[-1].id
    return rows, next_cursor


def statement_page(
    session: Session,
    member_id: str,
    *,
    cursor: int | None = None,
    limit: int = 50,
    kind: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> dict:
    rows, next_cursor = query_transactions(
        session,
        member_id,
        cursor=cursor,
        limit=limit,
        kind=kind,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return {
        "items": transactions_out(session, rows),
        "next_cursor": next_cursor,
        "balance_cents": balance_cents(session, member_id),
    }


def credit_summary(
    session: Session,
    member_id: str,
    *,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> dict:
    start, end = _parse_range(from_ts, to_ts)
    if start is None or end is None:
        raise ValueError("from 与 to 均为必填")

    q = select(CreditTransaction).where(
        CreditTransaction.member_id == member_id,
        CreditTransaction.created_at >= start,
        CreditTransaction.created_at < end,
        CreditTransaction.kind.in_(("charge", "refund")),
    )
    txns = list(session.scalars(q).all())

    total_charge = 0
    total_refund = 0
    by_day: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}

    charge_usage_ids = [t.usage_id for t in txns if t.kind == "charge" and t.usage_id]
    usages = {}
    if charge_usage_ids:
        usages = {
            row.id: row for row in session.scalars(select(ProxyKeyUsage).where(ProxyKeyUsage.id.in_(charge_usage_ids)))
        }

    for txn in txns:
        created = ensure_aware(txn.created_at) or txn.created_at
        day = created.astimezone(_BEIJING).strftime("%Y-%m-%d")
        bucket = by_day.setdefault(day, {"charge_cents": 0, "refund_cents": 0})
        if txn.kind == "charge":
            cents = abs(int(txn.amount_cents))
            total_charge += cents
            bucket["charge_cents"] += cents
            usage = usages.get(txn.usage_id) if txn.usage_id else None
            model = (usage.model if usage and usage.model else "unknown") or "unknown"
            model_row = by_model.setdefault(model, {"charge_cents": 0, "count": 0})
            model_row["charge_cents"] += cents
            model_row["count"] += 1
        elif txn.kind == "refund":
            cents = int(txn.amount_cents)
            total_refund += cents
            bucket["refund_cents"] += cents

    return {
        "from": serialize_datetime(start),
        "to": serialize_datetime(end),
        "total_charge_cents": total_charge,
        "total_refund_cents": total_refund,
        "by_day": [{"day": day, **values} for day, values in sorted(by_day.items())],
        "by_model": [
            {"model": model, **values}
            for model, values in sorted(by_model.items(), key=lambda item: (-item[1]["charge_cents"], item[0]))
        ],
    }


def csv_rows_for_transactions(txns: list[dict]) -> list[list[str]]:
    rows: list[list[str]] = [
        [
            "流水ID",
            "时间(北京时间)",
            "类型",
            "金额(USD)",
            "余额(USD)",
            "模型",
            "桶",
            "客户端",
            "来源",
            "输入",
            "输出",
            "缓存读",
            "缓存写",
            "推理",
            "备注",
        ]
    ]
    kind_labels = {
        "grant": "充值",
        "charge": "扣费",
        "refund": "退还",
        "adjust": "调整",
    }
    pool_labels = {"auto": "Auto", "api": "API"}
    client_labels = {"cli": "CLI", "ide": "IDE"}

    for item in txns:
        usage = item.get("usage") or {}
        tokens = usage.get("tokens") or {}
        created = item.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            local = dt.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            local = created
        amount_usd = f"{int(item['amount_cents']) / 100:.2f}"
        balance_usd = f"{int(item['balance_after_cents']) / 100:.2f}"
        source = usage.get("source") or {}
        rows.append(
            [
                str(item["id"]),
                local,
                kind_labels.get(item["kind"], item["kind"]),
                amount_usd,
                balance_usd,
                str(usage.get("model") or ""),
                pool_labels.get(usage.get("pool"), usage.get("pool") or ""),
                client_labels.get(usage.get("client"), usage.get("client") or ""),
                str(source.get("label") or ""),
                str(tokens.get("input", "")),
                str(tokens.get("output", "")),
                str(tokens.get("cache_read", "")),
                str(tokens.get("cache_write", "")),
                str(tokens.get("reasoning", "")),
                str(item.get("note") or ""),
            ]
        )
    return rows


def parse_plan_rules(items: list[dict] | None) -> list[dict]:
    rules = parse_spend_rules(items)
    return [rule.as_dict() for rule in rules]
