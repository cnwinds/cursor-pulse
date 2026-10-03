"""Membership plans, effective spend policy, and spend evaluation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy.credit import balance_cents
from pulse.proxy.spend_policy import (
    SpendPolicyConfigError,
    SpendRule,
    check_spend_rules,
    format_usd,
    legacy_loan_rules,
    loan_usage_cap_pool,
    parse_spend_rules,
    rules_from_json,
)
from pulse.storage.models import KeyLoan, Member, Membership, MembershipPlan, ProxyKey
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from pulse.util.datetime_fmt import ensure_aware

logger = logging.getLogger(__name__)

OPENING_CREDIT_NOTE = "membership:opening_credit"


class MembershipError(ValueError):
    """会员开通/变更参数无效。"""


@dataclass(frozen=True)
class EffectivePolicy:
    rules: list[SpendRule]
    credit_mode: str


def membership_required_for_member(session: Session, member_id: str | None) -> bool:
    if not member_id:
        return False
    member = session.get(Member, member_id)
    if member is None or not member.team_id:
        return False
    from pulse.proxy.team_membership import membership_required_for_team

    return membership_required_for_team(session, member.team_id)


def effective_policy(session: Session, membership: Membership) -> EffectivePolicy:
    override_rules = membership.rules_override
    if override_rules is not None:
        rules = rules_from_json(override_rules)
    elif membership.plan_id:
        plan = session.get(MembershipPlan, membership.plan_id)
        rules = rules_from_json(plan.rules if plan else None)
    else:
        rules = []

    mode_override = (membership.credit_mode_override or "").strip()
    if mode_override in ("unlimited", "prepaid"):
        credit_mode = mode_override
    elif membership.plan_id:
        plan = session.get(MembershipPlan, membership.plan_id)
        credit_mode = (plan.credit_mode if plan else "unlimited") or "unlimited"
    else:
        credit_mode = "unlimited"

    return EffectivePolicy(rules=rules, credit_mode=credit_mode)


def active_membership(session: Session, member_id: str) -> Membership | None:
    return session.scalar(
        select(Membership).where(
            Membership.member_id == member_id,
            Membership.status == "active",
        )
    )


def _has_opening_credit_grant(session: Session, member_id: str) -> bool:
    from pulse.storage.models import CreditTransaction

    row = session.scalar(
        select(CreditTransaction.id).where(
            CreditTransaction.member_id == member_id,
            CreditTransaction.kind == "grant",
            CreditTransaction.note == OPENING_CREDIT_NOTE,
        )
    )
    return row is not None


def _validate_credit_mode_override(value: str | None) -> str | None:
    if value is None:
        return None
    mode = (value or "").strip()
    if mode not in ("unlimited", "prepaid"):
        raise MembershipError("余额模式须为 unlimited 或 prepaid")
    return mode


def _normalize_rules_override(items: list | None) -> list[dict]:
    try:
        rules = parse_spend_rules(items)
    except SpendPolicyConfigError as exc:
        raise MembershipError(str(exc)) from exc
    return [rule.as_dict() for rule in rules]


def _validate_plan_for_member(
    session: Session,
    member_id: str,
    plan_id: str,
    *,
    assigning: bool,
) -> None:
    plan = session.get(MembershipPlan, plan_id)
    if plan is None:
        raise MembershipError("套餐不存在")
    member = session.get(Member, member_id)
    if member is None or member.team_id != plan.team_id:
        raise MembershipError("套餐不属于该成员的团队")
    if assigning and plan.status != "active":
        raise MembershipError("不能分配到已归档的套餐")


def open_membership(
    session: Session,
    *,
    member_id: str,
    plan_id: str | None,
    created_by_member_id: str | None,
    rules_override: list | None = None,
    credit_mode_override: str | None = None,
) -> Membership:
    """开通会员。套餐 ``opening_credit_cents`` 仅在该成员生命周期内赠送一次（见 ``OPENING_CREDIT_NOTE``）。"""
    existing = active_membership(session, member_id)
    if existing is not None:
        raise MembershipError("成员已有有效会员，请使用 change_membership")
    if plan_id is not None:
        _validate_plan_for_member(session, member_id, plan_id, assigning=True)
    normalized_rules = _normalize_rules_override(rules_override) if rules_override is not None else None
    normalized_mode = _validate_credit_mode_override(credit_mode_override)
    membership = Membership(
        member_id=member_id,
        plan_id=plan_id,
        rules_override=normalized_rules,
        credit_mode_override=normalized_mode,
        status="active",
        created_by_member_id=created_by_member_id,
    )
    session.add(membership)
    session.flush()
    _maybe_grant_opening_credit(session, membership)
    return membership


def change_membership(
    session: Session,
    membership: Membership,
    *,
    plan_id: str | None = None,
    rules_override: list | None = None,
    credit_mode_override: str | None = None,
    clear_rules_override: bool = False,
    clear_credit_mode_override: bool = False,
    clear_plan: bool = False,
) -> Membership:
    if clear_plan:
        membership.plan_id = None
    elif plan_id is not None:
        assigning = plan_id != membership.plan_id
        _validate_plan_for_member(session, membership.member_id, plan_id, assigning=assigning)
        membership.plan_id = plan_id
    if clear_rules_override:
        membership.rules_override = None
    elif rules_override is not None:
        membership.rules_override = _normalize_rules_override(rules_override)
    if clear_credit_mode_override:
        membership.credit_mode_override = None
    elif credit_mode_override is not None:
        membership.credit_mode_override = _validate_credit_mode_override(credit_mode_override)
    session.flush()
    _maybe_grant_opening_credit(session, membership)
    return membership


def cancel_membership(session: Session, membership: Membership) -> Membership:
    membership.status = "cancelled"
    session.flush()
    return membership


def _maybe_grant_opening_credit(session: Session, membership: Membership) -> None:
    if _has_opening_credit_grant(session, membership.member_id):
        return
    if not membership.plan_id:
        return
    plan = session.get(MembershipPlan, membership.plan_id)
    if plan is None or plan.opening_credit_cents is None or plan.opening_credit_cents <= 0:
        return
    from pulse.proxy.credit import grant

    grant(
        session,
        membership.member_id,
        plan.opening_credit_cents,
        actor_member_id=membership.created_by_member_id,
        note=OPENING_CREDIT_NOTE,
    )


def _resolve_member_id(
    session: Session,
    *,
    proxy_key_id: str | None,
    loan_id: str | None,
) -> str | None:
    if proxy_key_id:
        key = session.get(ProxyKey, proxy_key_id)
        return key.member_id if key else None
    if loan_id:
        loan = session.get(KeyLoan, loan_id)
        return loan.borrower_member_id if loan else None
    return None


def _credit_exhausted_message(balance: int) -> str:
    return (
        f"【小脉】余额已用完（当前余额 {format_usd(balance)}），请联系管理员充值。可在「我的会员 > 账单」查看每笔消耗。"
    )


def evaluate_spend(
    session: Session,
    *,
    proxy_key_id: str | None = None,
    loan_id: str | None = None,
    model: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Spend check for AgentService/Run.

    When both ``proxy_key_id`` and ``loan_id`` are provided (Go sends empty string
    for the unused field), member resolution prefers ``proxy_key_id``.
    """
    now = ensure_aware(now) or datetime.now(UTC)
    member_id = _resolve_member_id(session, proxy_key_id=proxy_key_id, loan_id=loan_id)

    request_pool = loan_usage_cap_pool(model)
    if request_pool is None:
        return {
            "status": "ok",
            "reason": "not_counted",
            "pool": None,
            "period": None,
            "used_cents": 0,
            "limit_cents": None,
            "resets_at": None,
            "other_pool": None,
            "other_pool_open": False,
            "message": "",
        }

    membership = active_membership(session, member_id) if member_id else None
    if membership is None:
        if member_id and membership_required_for_member(session, member_id):
            return {
                "status": "limited",
                "reason": "membership_required",
                "pool": None,
                "period": None,
                "used_cents": 0,
                "limit_cents": None,
                "resets_at": None,
                "other_pool": None,
                "other_pool_open": False,
                "message": "【小脉】未开通会员，请联系管理员开通后再使用。",
            }
        if loan_id and not member_id:
            loan = session.get(KeyLoan, loan_id)
            if loan is None or (getattr(loan, "delivery_mode", None) or "") != DELIVERY_PROXY_ALIAS:
                return {
                    "status": "ok",
                    "reason": "not_applicable",
                    "pool": None,
                    "period": None,
                    "used_cents": 0,
                    "limit_cents": None,
                    "resets_at": None,
                    "other_pool": None,
                    "other_pool_open": False,
                    "message": "",
                }
            legacy = legacy_loan_rules(loan)
            return check_spend_rules(
                session,
                legacy,
                model,
                loan_id=loan_id,
                now=now,
            )
        return {
            "status": "ok",
            "reason": None,
            "pool": None,
            "period": None,
            "used_cents": 0,
            "limit_cents": None,
            "resets_at": None,
            "other_pool": None,
            "other_pool_open": False,
            "message": "",
        }

    policy = effective_policy(session, membership)
    if policy.credit_mode == "prepaid":
        bal = balance_cents(session, member_id)
        if bal <= 0:
            return {
                "status": "limited",
                "reason": "credit_exhausted",
                "pool": request_pool,
                "period": None,
                "used_cents": 0,
                "limit_cents": None,
                "resets_at": None,
                "other_pool": None,
                "other_pool_open": False,
                "message": _credit_exhausted_message(bal),
            }

    return check_spend_rules(
        session,
        policy.rules,
        model,
        member_id=member_id,
        now=now,
    )


def batch_borrower_membership_context(
    session: Session,
    borrower_ids: set[str],
    *,
    now: datetime | None = None,
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Batch rule snapshots and membership summary for loan list rows."""
    from pulse.proxy.credit import balance_cents
    from pulse.proxy.spend_policy import rule_snapshots

    if not borrower_ids:
        return {}, {}

    memberships = {
        row.member_id: row
        for row in session.scalars(
            select(Membership).where(
                Membership.member_id.in_(borrower_ids),
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

    caps_by_member: dict[str, list[dict]] = {}
    summary_by_member: dict[str, dict] = {}
    for member_id, membership in memberships.items():
        policy = effective_policy(session, membership)
        caps_by_member[member_id] = rule_snapshots(session, policy.rules, member_id=member_id, now=now)
        plan = plans.get(membership.plan_id) if membership.plan_id else None
        summary_by_member[member_id] = {
            "plan_name": plan.name if plan else None,
            "credit_mode": policy.credit_mode,
            "balance_cents": balance_cents(session, member_id),
        }
    return caps_by_member, summary_by_member
