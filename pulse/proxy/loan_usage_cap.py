"""Per-loan rolling Auto/API spend caps (ADR-0004)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.pricing.billing_scope import is_auto_composer_model, is_likely_byok_model
from pulse.proxy.clock import WINDOW_7D, utcnow
from pulse.proxy.key_crud import cents_to_usd, usd_to_cents
from pulse.storage.models import KeyLoan, ProxyKeyUsage
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from pulse.util.datetime_fmt import ensure_aware

_CAP_PERIODS = frozenset({"5h", "week", "month"})
_BEIJING = ZoneInfo("Asia/Shanghai")

CapPool = Literal["auto", "api"]
UsageCapPeriod = Literal["5h", "week", "month"]


class UsageCapConfigError(ValueError):
    """Invalid usage-cap fields for issue or PATCH."""


@dataclass(frozen=True)
class CapRule:
    period: str
    pool: CapPool
    limit_cents: int

    def as_dict(self) -> dict:
        return {"period": self.period, "pool": self.pool, "limit_cents": self.limit_cents}


def loan_usage_cap_pool(model: str | None) -> CapPool | None:
    text = (model or "").strip()
    if not text:
        return "auto"
    if is_likely_byok_model(text):
        return None
    if is_auto_composer_model(text):
        return "auto"
    return "api"


def usage_cap_pool_column(model: str | None) -> str | None:
    """Value stored on ProxyKeyUsage.usage_cap_pool."""
    pool = loan_usage_cap_pool(model)
    if pool is None:
        return "skip"
    return pool


def cap_window_for_period(period: str) -> timedelta:
    if period == "5h":
        return timedelta(hours=5)
    if period == "week":
        return WINDOW_7D
    if period == "month":
        return timedelta(days=30)
    raise UsageCapConfigError(f"未知的记账周期：{period}")


def _legacy_rules(loan: KeyLoan) -> list[CapRule]:
    period = (getattr(loan, "usage_cap_period", None) or "").strip()
    if period not in _CAP_PERIODS:
        return []
    rules: list[CapRule] = []
    auto_cents = getattr(loan, "auto_cost_limit_cents", None)
    api_cents = getattr(loan, "api_cost_limit_cents", None)
    if auto_cents is not None:
        rules.append(CapRule(period, "auto", int(auto_cents)))
    if api_cents is not None:
        rules.append(CapRule(period, "api", int(api_cents)))
    return rules


def loan_cap_rules(loan: KeyLoan) -> list[CapRule]:
    """Stored rules, or the legacy single-period columns when rules were never written."""
    raw = getattr(loan, "usage_cap_rules", None)
    if isinstance(raw, list):
        rules: list[CapRule] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            period = str(item.get("period") or "").strip()
            pool = str(item.get("pool") or "").strip()
            cents = item.get("limit_cents")
            if period not in _CAP_PERIODS or pool not in ("auto", "api"):
                continue
            if not isinstance(cents, int) or cents < 1:
                continue
            rules.append(CapRule(period, pool, cents))  # type: ignore[arg-type]
        return rules
    return _legacy_rules(loan)


def usage_cap_enabled(loan: KeyLoan) -> bool:
    return bool(loan_cap_rules(loan))


def parse_usage_cap_rules(items: list[dict] | None) -> list[CapRule]:
    """API rows: period, pool, cost_usd. Empty list clears the cap."""
    rules: list[CapRule] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        period = str(item.get("period") or "").strip()
        pool = str(item.get("pool") or "").strip()
        cost = item.get("cost_usd")
        if period not in _CAP_PERIODS:
            raise UsageCapConfigError("记账周期须为 5h、week 或 month")
        if pool not in ("auto", "api"):
            raise UsageCapConfigError("用量限制须指定 Auto 或 API")
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 1:
            raise UsageCapConfigError("用量上限须为整数美元且至少 1")
        key = (period, pool)
        if key in seen:
            raise UsageCapConfigError("同一周期和同一桶只能有一条用量限制")
        seen.add(key)
        cents = usd_to_cents(cost)
        assert cents is not None
        rules.append(CapRule(period, pool, cents))  # type: ignore[arg-type]
    return rules


def apply_usage_cap_rules(loan: KeyLoan, rules: list[CapRule]) -> None:
    loan.usage_cap_rules = [rule.as_dict() for rule in rules]
    loan.usage_cap_period = None
    loan.auto_cost_limit_cents = None
    loan.api_cost_limit_cents = None


def clear_usage_cap_on_loan(loan: KeyLoan) -> None:
    apply_usage_cap_rules(loan, [])


def _effective_row_pool(row: ProxyKeyUsage) -> CapPool | None:
    stored = (row.usage_cap_pool or "").strip()
    if stored == "skip":
        return None
    if stored in ("auto", "api"):
        return stored  # type: ignore[return-value]
    return loan_usage_cap_pool(row.model)


def _window_cutoff(now: datetime, window: timedelta) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - window


def window_usage_events(
    session: Session,
    loan_id: str,
    *,
    window: timedelta,
    pool: CapPool,
    now: datetime | None = None,
) -> list[tuple[datetime, int]]:
    now = ensure_aware(now or utcnow()) or utcnow()
    cutoff = _window_cutoff(now, window)
    rows = session.scalars(
        select(ProxyKeyUsage)
        .where(
            ProxyKeyUsage.loan_id == loan_id,
            ProxyKeyUsage.ts > cutoff,
        )
        .order_by(ProxyKeyUsage.ts.asc())
    ).all()
    events: list[tuple[datetime, int]] = []
    for row in rows:
        if _effective_row_pool(row) != pool:
            continue
        ts = ensure_aware(row.ts) or row.ts
        events.append((ts, int(row.cost_cents or 0)))
    return events


def sum_window_usage_cents(
    session: Session,
    loan_id: str,
    *,
    window: timedelta,
    pool: CapPool,
    now: datetime | None = None,
) -> int:
    return sum(cents for _, cents in window_usage_events(session, loan_id, window=window, pool=pool, now=now))


def usage_resets_at(
    events: list[tuple[datetime, int]],
    limit: int,
    window: timedelta,
) -> datetime:
    dropped = 0
    total = sum(cents for _, cents in events)
    for ts, cents in events:
        dropped += cents
        if total - dropped < limit:
            ts_aware = ensure_aware(ts) or ts
            return ts_aware + window
    last_ts = ensure_aware(events[-1][0]) or events[-1][0]
    return last_ts + window


def _events_from_rows(
    rows: list[ProxyKeyUsage],
    *,
    pool: CapPool,
    cutoff: datetime,
) -> list[tuple[datetime, int]]:
    events: list[tuple[datetime, int]] = []
    for row in rows:
        if _effective_row_pool(row) != pool:
            continue
        ts = ensure_aware(row.ts) or row.ts
        if ts <= cutoff:
            continue
        events.append((ts, int(row.cost_cents or 0)))
    return events


def usage_cap_snapshots(
    session: Session,
    loans: list[KeyLoan],
    *,
    now: datetime | None = None,
) -> dict[str, list[dict]]:
    """Per loan, one row per rule with current-window used cents."""
    now = ensure_aware(now or utcnow()) or utcnow()
    enabled = [(loan, loan_cap_rules(loan)) for loan in loans if loan_cap_rules(loan)]
    if not enabled:
        return {loan.id: [] for loan in loans}

    min_cutoff = now
    windows: dict[str, list[tuple[CapRule, timedelta, datetime]]] = {}
    for loan, rules in enabled:
        packed: list[tuple[CapRule, timedelta, datetime]] = []
        for rule in rules:
            window = cap_window_for_period(rule.period)
            cutoff = _window_cutoff(now, window)
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


def _period_label(period: str) -> str:
    if period == "5h":
        return "滚动 5 小时"
    if period == "week":
        return "滚动 7 天"
    if period == "month":
        return "滚动 30 天"
    return period


def _pool_label(pool: CapPool) -> str:
    return "Auto" if pool == "auto" else "API"


def _format_usd(cents: int) -> str:
    amount = int(cents)
    sign = "-" if amount < 0 else ""
    dollars, rem = divmod(abs(amount), 100)
    if rem == 0:
        return f"{sign}${dollars}"
    return f"{sign}${dollars}.{rem:02d}"


def _format_relative_until(target: datetime, now: datetime) -> str:
    target = ensure_aware(target) or target
    now = ensure_aware(now) or now
    delta = target - now
    secs = max(0, int(delta.total_seconds()))
    days = secs // 86400
    secs %= 86400
    hours = secs // 3600
    secs %= 3600
    minutes = secs // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    if minutes and not days:
        parts.append(f"{minutes} 分钟")
    if not parts:
        return "即将"
    return "约 " + " ".join(parts) + "后"


def _format_beijing_wall(resets_at: datetime) -> str:
    dt = ensure_aware(resets_at) or resets_at
    local = dt.astimezone(_BEIJING)
    return local.strftime("%Y-%m-%d %H:%M")


def _other_pool(pool: CapPool) -> CapPool:
    return "api" if pool == "auto" else "auto"


def _rule_clause(rule: CapRule, used: int, resets_at: datetime, now: datetime) -> str:
    rel = _format_relative_until(resets_at, now)
    wall = _format_beijing_wall(resets_at)
    return (
        f"{_period_label(rule.period)} {_format_usd(used)} / {_format_usd(rule.limit_cents)}"
        f"（{rel}恢复，{wall} 北京时间）"
    )


def _build_limited_message(
    pool: CapPool,
    exceeded: list[tuple[CapRule, int, datetime]],
    *,
    other_open: bool,
    now: datetime,
) -> str:
    clauses = "；".join(_rule_clause(rule, used, resets_at, now) for rule, used, resets_at in exceeded)
    if len(exceeded) == 1:
        head = f"【小脉借用】{_pool_label(pool)} 额度已用尽（{clauses}）。"
    else:
        head = f"【小脉借用】{_pool_label(pool)} 已触及多条限制（任一达到即停）：{clauses}。"
    if other_open:
        other = _other_pool(pool)
        if pool == "auto":
            return head + f"请改用 {_pool_label(other)} 模型，或等到恢复后再用 {_pool_label(pool)}。"
        return head + f"请改用 {_pool_label(other)} / Composer，或等到恢复后再用 {_pool_label(pool)}。"
    return head


def _ok_payload(
    *,
    reason: str | None = None,
) -> dict:
    return {
        "status": "ok",
        "reason": reason,
        "pool": None,
        "period": None,
        "used_cents": 0,
        "limit_cents": None,
        "resets_at": None,
        "other_pool": None,
        "other_pool_open": False,
        "message": "",
    }


def check_loan_usage_cap(
    session: Session,
    loan_id: str,
    model: str | None,
    *,
    now: datetime | None = None,
) -> dict:
    now = ensure_aware(now or utcnow()) or utcnow()
    loan = session.get(KeyLoan, loan_id)
    if loan is None:
        return _ok_payload(reason="not_applicable")
    if (getattr(loan, "delivery_mode", None) or "") != DELIVERY_PROXY_ALIAS:
        return _ok_payload(reason="not_applicable")
    if loan.status != "active":
        return _ok_payload(reason="not_applicable")

    rules = loan_cap_rules(loan)
    if not rules:
        return _ok_payload(reason="cap_disabled")

    request_pool = loan_usage_cap_pool(model)
    if request_pool is None:
        return _ok_payload(reason="not_counted")

    def exceeded_for(pool: CapPool) -> list[tuple[CapRule, int, datetime]]:
        hit: list[tuple[CapRule, int, datetime]] = []
        for rule in rules:
            if rule.pool != pool:
                continue
            window = cap_window_for_period(rule.period)
            events = window_usage_events(session, loan_id, window=window, pool=pool, now=now)
            used = sum(cents for _, cents in events)
            if used < rule.limit_cents:
                continue
            if not events:
                continue
            hit.append((rule, used, usage_resets_at(events, rule.limit_cents, window)))
        return hit

    exceeded = exceeded_for(request_pool)
    if not exceeded:
        return _ok_payload(reason=None)

    other = _other_pool(request_pool)
    other_open = not exceeded_for(other)
    # A pool with no rules is still open.
    if not any(rule.pool == other for rule in rules):
        other_open = True

    resets = max(item[2] for item in exceeded)
    primary = max(exceeded, key=lambda item: item[2])
    message = _build_limited_message(request_pool, exceeded, other_open=other_open, now=now)
    return {
        "status": "limited",
        "reason": "loan_usage_cap_exceeded",
        "pool": request_pool,
        "period": primary[0].period,
        "used_cents": primary[1],
        "limit_cents": primary[0].limit_cents,
        "resets_at": resets.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "other_pool": other,
        "other_pool_open": other_open,
        "message": message,
    }
