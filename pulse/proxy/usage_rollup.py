"""Roll up ProxyKeyUsage rows into by_account / by_model / by_day views."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import (
    AiAccount,
    AiAccountCredential,
    AiPlan,
    Member,
    ProxyKeyUsage,
)
from pulse.util.datetime_fmt import format_china_date, serialize_datetime

_UNKNOWN_ACCOUNT_LABEL = "未知账号"
_UNKNOWN_MODEL_LABEL = "（未知）"
_UNKNOWN_DAY_LABEL = "未知"


class ProxyUsageRollup(TypedDict):
    by_account: list[dict]
    by_model: list[dict]
    by_day: list[dict]
    items: list[dict]


def rollup_proxy_usages(
    session: Session,
    rows: Sequence[ProxyKeyUsage],
    *,
    limit: int = 50,
) -> ProxyUsageRollup:
    """Aggregate usage rows behind one interface.

    ``items`` is truncated to ``limit`` (newest-first order of ``rows``
    preserved); rollups use all rows.
    """
    cred_to_account, accounts, plans, members = _resolve_usage_accounts(session, rows)

    def primary_name(acct: AiAccount | None) -> str | None:
        if not acct or not acct.primary_member_id:
            return None
        return members.get(acct.primary_member_id)

    by_account_map: dict[str, dict] = {}
    by_model_map: dict[str, dict] = {}
    by_day_map: dict[str, dict] = {}
    items_all: list[dict] = []

    for u in rows:
        account_id = cred_to_account.get(u.credential_id) if u.credential_id else None
        acct = accounts.get(account_id) if account_id else None
        item = {
            "id": u.id,
            "credential_id": u.credential_id,
            "account_id": account_id,
            "account_identifier": acct.account_identifier if acct else None,
            "primary_member_name": primary_name(acct),
            "model": u.model,
            "tokens_input": u.tokens_input,
            "tokens_output": u.tokens_output,
            "tokens_cache_read": u.tokens_cache_read,
            "tokens_cache_write": u.tokens_cache_write,
            "tokens_reasoning": u.tokens_reasoning,
            "total_tokens": u.total_tokens,
            "cost_cents": u.cost_cents,
            "ts": serialize_datetime(u.ts),
        }
        items_all.append(item)

        tokens = int(u.total_tokens or 0)
        cost = int(u.cost_cents or 0)

        bucket_key = account_id or "__unknown__"
        if bucket_key not in by_account_map:
            by_account_map[bucket_key] = {
                "account_id": account_id,
                "account_identifier": (
                    acct.account_identifier if acct else _UNKNOWN_ACCOUNT_LABEL
                ),
                "primary_member_name": primary_name(acct),
                "plan_name": plans.get(acct.plan_id) if acct else None,
                "request_count": 0,
                "total_tokens": 0,
                "cost_cents": 0,
            }
        account_bucket = by_account_map[bucket_key]
        account_bucket["request_count"] += 1
        account_bucket["total_tokens"] += tokens
        account_bucket["cost_cents"] += cost

        label = (u.model or "").strip() or _UNKNOWN_MODEL_LABEL
        model_bucket = by_model_map.get(label)
        if model_bucket is None:
            model_bucket = {
                "model": label,
                "request_count": 0,
                "total_tokens": 0,
                "cost_cents": 0,
            }
            by_model_map[label] = model_bucket
        model_bucket["request_count"] += 1
        model_bucket["total_tokens"] += tokens
        model_bucket["cost_cents"] += cost

        day = format_china_date(u.ts) or _UNKNOWN_DAY_LABEL
        day_bucket = by_day_map.get(day)
        if day_bucket is None:
            day_bucket = {
                "day": day,
                "request_count": 0,
                "total_tokens": 0,
                "cost_cents": 0,
                "items": [],
            }
            by_day_map[day] = day_bucket
        day_bucket["request_count"] += 1
        day_bucket["total_tokens"] += tokens
        day_bucket["cost_cents"] += cost
        day_bucket["items"].append(item)

    return {
        "by_account": sorted(
            by_account_map.values(),
            key=lambda r: r["total_tokens"],
            reverse=True,
        ),
        "by_model": sorted(
            by_model_map.values(),
            key=lambda r: r["cost_cents"],
            reverse=True,
        ),
        # Newest China calendar day first; rows without ts last.
        "by_day": sorted(
            by_day_map.values(),
            key=lambda r: (0 if r["day"] == _UNKNOWN_DAY_LABEL else 1, r["day"]),
            reverse=True,
        ),
        "items": items_all[:limit],
    }


def _resolve_usage_accounts(
    session: Session,
    rows: Sequence[ProxyKeyUsage],
) -> tuple[dict[str, str], dict[str, AiAccount], dict[str, str], dict[str, str]]:
    cred_ids = {u.credential_id for u in rows if u.credential_id}
    cred_to_account: dict[str, str] = {}
    if cred_ids:
        for cred in session.execute(
            select(AiAccountCredential).where(AiAccountCredential.id.in_(cred_ids))
        ).scalars():
            cred_to_account[cred.id] = cred.account_id

    account_ids = set(cred_to_account.values())
    accounts: dict[str, AiAccount] = {}
    plans: dict[str, str] = {}
    members: dict[str, str] = {}
    if not account_ids:
        return cred_to_account, accounts, plans, members

    for acct in session.execute(
        select(AiAccount).where(AiAccount.id.in_(account_ids))
    ).scalars():
        accounts[acct.id] = acct
    plan_ids = {a.plan_id for a in accounts.values()}
    if plan_ids:
        for plan in session.execute(select(AiPlan).where(AiPlan.id.in_(plan_ids))).scalars():
            plans[plan.id] = plan.plan_name
    member_ids = {a.primary_member_id for a in accounts.values() if a.primary_member_id}
    if member_ids:
        for m in session.execute(select(Member).where(Member.id.in_(member_ids))).scalars():
            members[m.id] = m.display_name
    return cred_to_account, accounts, plans, members
