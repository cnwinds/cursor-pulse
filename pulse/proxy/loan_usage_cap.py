"""Per-loan rolling Auto/API spend caps (ADR-0004) — compatibility layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy import spend_policy as _spend_policy
from pulse.proxy.spend_policy import (
    SpendPolicyConfigError,
    SpendRule,
    cap_window_for_period,
    legacy_loan_rules,
    parse_spend_rules,
    usage_resets_at,
)
from pulse.storage.models import KeyLoan, ProxyKeyUsage

loan_usage_cap_pool = _spend_policy.loan_usage_cap_pool
usage_cap_pool_column = _spend_policy.usage_cap_pool_column
window_usage_events = _spend_policy.window_usage_events

CapPool = str
UsageCapConfigError = SpendPolicyConfigError
CapRule = SpendRule


def loan_cap_rules(loan: KeyLoan) -> list[SpendRule]:
    return legacy_loan_rules(loan)


def usage_cap_enabled(loan: KeyLoan) -> bool:
    return bool(loan_cap_rules(loan))


def parse_usage_cap_rules(items: list[dict] | None) -> list[SpendRule]:
    return parse_spend_rules(items)


def apply_usage_cap_rules(loan: KeyLoan, rules: list[SpendRule]) -> None:
    loan.usage_cap_rules = [rule.as_dict() for rule in rules]
    loan.usage_cap_period = None
    loan.auto_cost_limit_cents = None
    loan.api_cost_limit_cents = None


def clear_usage_cap_on_loan(loan: KeyLoan) -> None:
    apply_usage_cap_rules(loan, [])


def sum_window_usage_cents(
    session: Session,
    loan_id: str,
    *,
    window: timedelta,
    pool: CapPool,
    now: datetime | None = None,
) -> int:
    from pulse.proxy.spend_policy import sum_window_usage_cents as _sum

    return _sum(session, loan_id=loan_id, window=window, pool=pool, now=now)


def usage_cap_snapshots(
    session: Session,
    loans: list[KeyLoan],
    *,
    now: datetime | None = None,
) -> dict[str, list[dict]]:
    """Per loan, one row per rule with current-window used cents."""
    from pulse.proxy.clock import utcnow
    from pulse.proxy.key_crud import cents_to_usd
    from pulse.proxy.spend_policy import _events_from_rows
    from pulse.util.datetime_fmt import ensure_aware

    now = ensure_aware(now or utcnow()) or utcnow()
    enabled = [(loan, loan_cap_rules(loan)) for loan in loans if loan_cap_rules(loan)]
    if not enabled:
        return {loan.id: [] for loan in loans}

    min_cutoff = now
    windows: dict[str, list[tuple[SpendRule, timedelta, datetime]]] = {}
    for loan, rules in enabled:
        packed: list[tuple[SpendRule, timedelta, datetime]] = []
        for rule in rules:
            window = cap_window_for_period(rule.period)
            cutoff = now - window
            packed.append((rule, window, cutoff))
            if cutoff < min_cutoff:
                min_cutoff = cutoff
        windows[loan.id] = packed

    rows = session.scalars(
        select(ProxyKeyUsage)
        .where(
            ProxyKeyUsage.loan_id.in_(list(windows)),
            ProxyKeyUsage.ts > min_cutoff,
        )
        .order_by(ProxyKeyUsage.ts.asc())
    ).all()
    by_loan: dict[str, list[ProxyKeyUsage]] = {loan_id: [] for loan_id in windows}
    for row in rows:
        if row.loan_id in by_loan:
            by_loan[row.loan_id].append(row)

    out: dict[str, list[dict]] = {loan.id: [] for loan in loans}
    for loan, _rules in enabled:
        snapshots: list[dict] = []
        for rule, window, cutoff in windows[loan.id]:
            events = _events_from_rows(by_loan.get(loan.id, []), pool=rule.pool, cutoff=cutoff)
            used = sum(cents for _, cents in events)
            resets_at = None
            if events:
                resets_at = (
                    usage_resets_at(events, rule.limit_cents, window).astimezone(UTC).isoformat().replace("+00:00", "Z")
                )
            snapshots.append(
                {
                    "period": rule.period,
                    "pool": rule.pool,
                    "limit_cents": rule.limit_cents,
                    "cost_usd": cents_to_usd(rule.limit_cents),
                    "used_cents": used,
                    "exceeded": used >= rule.limit_cents,
                    "resets_at": resets_at,
                }
            )
        out[loan.id] = snapshots
    return out


def check_loan_usage_cap(
    session: Session,
    loan_id: str,
    model: str | None,
    *,
    now: datetime | None = None,
) -> dict:
    from pulse.proxy.membership import evaluate_spend

    result = evaluate_spend(session, loan_id=loan_id, model=model, now=now)
    if result.get("reason") == "spend_rule_exceeded":
        return {**result, "reason": "loan_usage_cap_exceeded"}
    return result
