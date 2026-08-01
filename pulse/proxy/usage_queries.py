"""Read-only Usage Ledger queries (no authorize / suspend side effects).

Key Loan presentation imports from here instead of ``usage.py`` so the
lifecycle modules do not depend on the write ledger.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.storage.models import ProxyKeyUsage


def loan_proxy_totals(session: Session, loan_id: str) -> tuple[int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        ).where(ProxyKeyUsage.loan_id == loan_id)
    ).one()
    return int(row[0]), int(row[1])
