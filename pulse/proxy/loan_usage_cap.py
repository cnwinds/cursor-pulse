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
class ParsedUsageCap:
    usage_cap_period: str | None
    auto_cost_limit_cents: int | None
    api_cost_limit_cents: int | None


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


def usage_cap_enabled(loan: KeyLoan) -> bool:
    period = (loan.usage_cap_period or "").strip()
    if not period:
        return False
    return loan.auto_cost_limit_cents is not None or loan.api_cost_limit_cents is not None


def parse_usage_cap_fields(
    *,
    usage_cap_period: str | None = None,
    auto_cost_usd: int | None = None,
    api_cost_usd: int | None = None,
) -> ParsedUsageCap:
    period = (usage_cap_period or "").strip() or None
    auto_cents = usd_to_cents(auto_cost_usd) if auto_cost_usd is not None else None
    api_cents = usd_to_cents(api_cost_usd) if api_cost_usd is not None else None

    if period is None and auto_cents is None and api_cents is None:
        return ParsedUsageCap(None, None, None)

    if period is None or (auto_cents is None and api_cents is None):
        raise UsageCapConfigError("用量封顶须同时指定记账周期与至少一个桶的上限（美元整数 ≥ 1）")

    if period not in _CAP_PERIODS:
        raise UsageCapConfigError("记账周期须为 5h、week 或 month")

    if auto_cost_usd is not None and auto_cost_usd < 1:
        raise UsageCapConfigError("Auto 上限须为整数美元且至少 1")
    if api_cost_usd is not None and api_cost_usd < 1:
        raise UsageCapConfigError("API 上限须为整数美元且至少 1")

    return ParsedUsageCap(period, auto_cents, api_cents)


def apply_usage_cap_to_loan(loan: KeyLoan, parsed: ParsedUsageCap) -> None:
    loan.usage_cap_period = parsed.usage_cap_period
    loan.auto_cost_limit_cents = parsed.auto_cost_limit_cents
    loan.api_cost_limit_cents = parsed.api_cost_limit_cents


def clear_usage_cap_on_loan(loan: KeyLoan) -> None:
    loan.usage_cap_period = None
    loan.auto_cost_limit_cents = None
    loan.api_cost_limit_cents = None


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


def batch_window_usage_cents(
    session: Session,
    loans: list[KeyLoan],
    *,
    now: datetime | None = None,
) -> dict[str, tuple[int | None, int | None]]:
    """Map loan_id → (auto_used, api_used) for cap-enabled loans; else (None, None)."""
    now = ensure_aware(now or utcnow()) or utcnow()
    enabled = [loan for loan in loans if usage_cap_enabled(loan)]
    if not enabled:
        return {loan.id: (None, None) for loan in loans}

    loan_ids = [loan.id for loan in enabled]
    min_cutoff = now
    per_loan_window: dict[str, timedelta] = {}
    for loan in enabled:
        period = (loan.usage_cap_period or "").strip()
        window = cap_window_for_period(period)
        per_loan_window[loan.id] = window
        cutoff = _window_cutoff(now, window)
        if cutoff < min_cutoff:
            min_cutoff = cutoff

    rows = session.scalars(
        select(ProxyKeyUsage)
        .where(
            ProxyKeyUsage.loan_id.in_(loan_ids),
            ProxyKeyUsage.ts > min_cutoff,
        )
        .order_by(ProxyKeyUsage.ts.asc())
    ).all()

    sums: dict[str, dict[CapPool, int]] = {lid: {"auto": 0, "api": 0} for lid in loan_ids}
    for row in rows:
        lid = row.loan_id
        if lid not in per_loan_window:
            continue
        ts = ensure_aware(row.ts) or row.ts
        if ts <= _window_cutoff(now, per_loan_window[lid]):
            continue
        pool = _effective_row_pool(row)
        if pool is None:
            continue
        sums[lid][pool] += int(row.cost_cents or 0)

    out: dict[str, tuple[int | None, int | None]] = {}
    for loan in loans:
        if not usage_cap_enabled(loan):
            out[loan.id] = (None, None)
        else:
            s = sums.get(loan.id, {"auto": 0, "api": 0})
            out[loan.id] = (s["auto"], s["api"])
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
    return f"${cents_to_usd(cents) or 0}"


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


def _pool_open(
    loan: KeyLoan,
    pool: CapPool,
    used: int,
) -> bool:
    limit = loan.auto_cost_limit_cents if pool == "auto" else loan.api_cost_limit_cents
    if limit is None:
        return True
    return used < limit


def _build_limited_message(
    loan: KeyLoan,
    pool: CapPool,
    *,
    used_cents: int,
    limit_cents: int,
    resets_at: datetime,
    other_open: bool,
    now: datetime,
) -> str:
    period = (loan.usage_cap_period or "").strip()
    period_label = _period_label(period)
    rel = _format_relative_until(resets_at, now)
    wall = _format_beijing_wall(resets_at)
    head = (
        f"【小脉借用】{_pool_label(pool)} 额度已用尽"
        f"（{_format_usd(used_cents)} / {_format_usd(limit_cents)}，{period_label}）。"
        f"{rel}恢复（{wall} 北京时间）。"
    )
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

    if not usage_cap_enabled(loan):
        return _ok_payload(reason="cap_disabled")

    request_pool = loan_usage_cap_pool(model)
    if request_pool is None:
        return _ok_payload(reason="not_counted")

    period = (loan.usage_cap_period or "").strip()
    window = cap_window_for_period(period)
    auto_used = sum_window_usage_cents(session, loan_id, window=window, pool="auto", now=now)
    api_used = sum_window_usage_cents(session, loan_id, window=window, pool="api", now=now)

    limit = loan.auto_cost_limit_cents if request_pool == "auto" else loan.api_cost_limit_cents
    used = auto_used if request_pool == "auto" else api_used

    if limit is None or used < limit:
        return _ok_payload(reason=None)

    events = window_usage_events(session, loan_id, window=window, pool=request_pool, now=now)
    resets = usage_resets_at(events, limit, window)
    other = _other_pool(request_pool)
    other_used = api_used if request_pool == "auto" else auto_used
    other_open = _pool_open(loan, other, other_used)

    message = _build_limited_message(
        loan,
        request_pool,
        used_cents=used,
        limit_cents=limit,
        resets_at=resets,
        other_open=other_open,
        now=now,
    )
    resets_iso = resets.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "status": "limited",
        "reason": "loan_usage_cap_exceeded",
        "pool": request_pool,
        "period": period,
        "used_cents": used,
        "limit_cents": limit,
        "resets_at": resets_iso,
        "other_pool": other,
        "other_pool_open": other_open,
        "message": message,
    }


def usage_cap_resets_at_for_loan(
    session: Session,
    loan: KeyLoan,
    *,
    auto_used: int,
    api_used: int,
    now: datetime | None = None,
) -> str | None:
    """Earliest resets_at among exceeded configured buckets, for list payloads."""
    if not usage_cap_enabled(loan):
        return None
    now = ensure_aware(now or utcnow()) or utcnow()
    period = (loan.usage_cap_period or "").strip()
    window = cap_window_for_period(period)
    candidates: list[datetime] = []
    for pool, used, limit in (
        ("auto", auto_used, loan.auto_cost_limit_cents),
        ("api", api_used, loan.api_cost_limit_cents),
    ):
        if limit is None or used < limit:
            continue
        events = window_usage_events(session, loan.id, window=window, pool=pool, now=now)
        if not events:
            continue
        candidates.append(usage_resets_at(events, limit, window))
    if not candidates:
        return None
    earliest = min(candidates)
    return earliest.astimezone(UTC).isoformat().replace("+00:00", "Z")
