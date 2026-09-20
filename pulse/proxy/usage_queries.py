"""Read-only Usage Ledger queries (no authorize / suspend side effects).

Key Loan presentation imports from here instead of ``usage.py`` so the
lifecycle modules do not depend on the write ledger.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.storage.models import ProxyKeyUsage


def last_loan_usage_at(session: Session, loan_ids: list[str]) -> dict[str, datetime]:
    """每个借用最近一次代理流量的时刻（UTC）。

    Auto Lender 换绑前用它判断该借用是否正在被使用，避免打断在用会话。
    """
    ids = [loan_id for loan_id in loan_ids if loan_id]
    if not ids:
        return {}
    rows = session.execute(
        select(ProxyKeyUsage.loan_id, func.max(ProxyKeyUsage.ts))
        .where(ProxyKeyUsage.loan_id.in_(ids))
        .group_by(ProxyKeyUsage.loan_id)
    )
    out: dict[str, datetime] = {}
    for loan_id, ts in rows:
        if loan_id is None or ts is None:
            continue
        out[loan_id] = ts
    return out


def loan_proxy_totals_by_loan(
    session: Session, loan_ids: list[str]
) -> dict[str, tuple[int, int]]:
    ids = [loan_id for loan_id in loan_ids if loan_id]
    if not ids:
        return {}
    rows = session.execute(
        select(
            ProxyKeyUsage.loan_id,
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        )
        .where(ProxyKeyUsage.loan_id.in_(ids))
        .group_by(ProxyKeyUsage.loan_id)
    )
    return {loan_id: (int(tokens), int(cents)) for loan_id, tokens, cents in rows}


def loan_proxy_totals(session: Session, loan_id: str) -> tuple[int, int]:
    return loan_proxy_totals_by_loan(session, [loan_id]).get(loan_id, (0, 0))


def active_proxy_key_usage_totals(session: Session) -> tuple[int, int, int]:
    """Return (active_key_count, total_tokens, total_cost_cents)."""
    from pulse.storage.models import ProxyKey

    count = (
        session.scalar(
            select(func.count()).select_from(ProxyKey).where(ProxyKey.status == "active")
        )
        or 0
    )
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        )
        .select_from(ProxyKeyUsage)
        .join(ProxyKey, ProxyKeyUsage.proxy_key_id == ProxyKey.id)
        .where(ProxyKey.status == "active")
    ).one()
    return int(count), int(row[0]), int(row[1])
