"""Member- or loan-scoped rolling spend rules (ADR-0004 generalized)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.pricing.billing_scope import is_auto_composer_model, is_likely_byok_model
from pulse.proxy.clock import WINDOW_7D, utcnow
from pulse.proxy.key_crud import usd_to_cents
from pulse.storage.models import KeyLoan, ProxyKeyUsage
from pulse.util.datetime_fmt import ensure_aware

_SPEND_PERIODS = frozenset({"5h", "week", "month"})
_BEIJING = ZoneInfo("Asia/Shanghai")

CapPool = Literal["auto", "api"]
SpendPool = Literal["auto", "api", "total"]
UsageCapPeriod = Literal["5h", "week", "month"]


class SpendPolicyConfigError(ValueError):
    """Invalid spend-rule fields."""


@dataclass(frozen=True)
class SpendRule:
    period: str
    pool: SpendPool
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
    raise SpendPolicyConfigError(f"未知的记账周期：{period}")


def parse_spend_rules(items: list[dict] | None) -> list[SpendRule]:
    rules: list[SpendRule] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        period = str(item.get("period") or "").strip()
        pool = str(item.get("pool") or "").strip()
        limit = item.get("limit_cents")
        cost = item.get("cost_usd")
        if limit is None and cost is not None:
            if not isinstance(cost, int) or isinstance(cost, bool) or cost < 1:
                raise SpendPolicyConfigError("用量上限须为整数美元且至少 1")
            limit = usd_to_cents(cost)
        if period not in _SPEND_PERIODS:
            raise SpendPolicyConfigError("记账周期须为 5h、week 或 month")
        if pool not in ("auto", "api", "total"):
            raise SpendPolicyConfigError("用量限制须指定 Auto、API 或合计")
        if not isinstance(limit, int) or limit < 100:
            raise SpendPolicyConfigError("用量上限须至少 1 美元（100 美分）")
        key = (period, pool)
        if key in seen:
            raise SpendPolicyConfigError("同一周期和同一桶只能有一条用量限制")
        seen.add(key)
        rules.append(SpendRule(period, pool, int(limit)))  # type: ignore[arg-type]
    return rules


def rules_from_json(raw: list | None) -> list[SpendRule]:
    if not isinstance(raw, list):
        return []
    rules: list[SpendRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period") or "").strip()
        pool = str(item.get("pool") or "").strip()
        cents = item.get("limit_cents")
        if period not in _SPEND_PERIODS or pool not in ("auto", "api", "total"):
            continue
        if not isinstance(cents, int) or cents < 100:
            continue
        rules.append(SpendRule(period, pool, cents))  # type: ignore[arg-type]
    return rules


def legacy_loan_rules(loan: KeyLoan) -> list[SpendRule]:
    """Stored loan rules, or legacy single-period columns when rules were never written."""
    raw = getattr(loan, "usage_cap_rules", None)
    if isinstance(raw, list):
        return rules_from_json(raw)
    period = (getattr(loan, "usage_cap_period", None) or "").strip()
    if period not in _SPEND_PERIODS:
        return []
    rules: list[SpendRule] = []
    auto_cents = getattr(loan, "auto_cost_limit_cents", None)
    api_cents = getattr(loan, "api_cost_limit_cents", None)
    if auto_cents is not None:
        rules.append(SpendRule(period, "auto", int(auto_cents)))
    if api_cents is not None:
        rules.append(SpendRule(period, "api", int(api_cents)))
    return rules


def min_merge_rules(existing: list[SpendRule], incoming: list[SpendRule]) -> list[SpendRule]:
    merged: dict[tuple[str, str], int] = {}
    for rule in existing + incoming:
        key = (rule.period, rule.pool)
        merged[key] = min(merged.get(key, rule.limit_cents), rule.limit_cents)
    return [SpendRule(period, pool, limit) for (period, pool), limit in sorted(merged.items())]


def _effective_row_pool(row: ProxyKeyUsage) -> CapPool | None:
    stored = (row.usage_cap_pool or "").strip()
    if stored == "skip":
        return None
    if stored in ("auto", "api"):
        return stored  # type: ignore[return-value]
    return loan_usage_cap_pool(row.model)


def _row_matches_pool(row: ProxyKeyUsage, pool: SpendPool) -> bool:
    effective = _effective_row_pool(row)
    if effective is None:
        return False
    if pool == "total":
        return effective in ("auto", "api")
    return effective == pool


def _window_cutoff(now: datetime, window: timedelta) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - window


def _scope_filter(member_id: str | None, loan_id: str | None):
    if member_id:
        return ProxyKeyUsage.member_id == member_id
    if loan_id:
        return ProxyKeyUsage.loan_id == loan_id
    raise ValueError("member_id or loan_id required")


def window_usage_events(
    session: Session,
    *,
    member_id: str | None = None,
    loan_id: str | None = None,
    window: timedelta,
    pool: SpendPool,
    now: datetime | None = None,
) -> list[tuple[datetime, int]]:
    now = ensure_aware(now or utcnow()) or utcnow()
    cutoff = _window_cutoff(now, window)
    rows = session.scalars(
        select(ProxyKeyUsage)
        .where(
            _scope_filter(member_id, loan_id),
            ProxyKeyUsage.ts > cutoff,
        )
        .order_by(ProxyKeyUsage.ts.asc())
    ).all()
    events: list[tuple[datetime, int]] = []
    for row in rows:
        if not _row_matches_pool(row, pool):
            continue
        ts = ensure_aware(row.ts) or row.ts
        events.append((ts, int(row.cost_cents or 0)))
    return events


def sum_window_usage_cents(
    session: Session,
    *,
    member_id: str | None = None,
    loan_id: str | None = None,
    window: timedelta,
    pool: SpendPool,
    now: datetime | None = None,
) -> int:
    return sum(
        cents
        for _, cents in window_usage_events(
            session,
            member_id=member_id,
            loan_id=loan_id,
            window=window,
            pool=pool,
            now=now,
        )
    )


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
    pool: SpendPool,
    cutoff: datetime,
) -> list[tuple[datetime, int]]:
    events: list[tuple[datetime, int]] = []
    for row in rows:
        if not _row_matches_pool(row, pool):
            continue
        ts = ensure_aware(row.ts) or row.ts
        if ts <= cutoff:
            continue
        events.append((ts, int(row.cost_cents or 0)))
    return events


def _period_label(period: str) -> str:
    if period == "5h":
        return "滚动 5 小时"
    if period == "week":
        return "滚动 7 天"
    if period == "month":
        return "滚动 30 天"
    return period


def _pool_label(pool: SpendPool | CapPool) -> str:
    if pool == "auto":
        return "Auto"
    if pool == "api":
        return "API"
    return "合计"


def format_usd(cents: int) -> str:
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


def _other_pool(pool: SpendPool) -> CapPool:
    if pool == "auto":
        return "api"
    if pool == "api":
        return "auto"
    return "auto"


def _rule_clause(rule: SpendRule, used: int, resets_at: datetime, now: datetime) -> str:
    rel = _format_relative_until(resets_at, now)
    wall = _format_beijing_wall(resets_at)
    return (
        f"{_period_label(rule.period)} {format_usd(used)} / {format_usd(rule.limit_cents)}"
        f"（{rel}恢复，{wall} 北京时间）"
    )


def _build_limited_message(
    pool: SpendPool,
    exceeded: list[tuple[SpendRule, int, datetime]],
    *,
    other_open: bool,
    now: datetime,
) -> str:
    clauses = "；".join(_rule_clause(rule, used, resets_at, now) for rule, used, resets_at in exceeded)
    if len(exceeded) == 1:
        head = f"【小脉】{_pool_label(pool)} 额度已用尽（{clauses}）。"
    else:
        head = f"【小脉】{_pool_label(pool)} 已触及多条限制（任一达到即停）：{clauses}。"
    if other_open:
        other = _other_pool(pool)
        if pool == "auto":
            return head + f"请改用 {_pool_label(other)} 模型，或等到恢复后再用 {_pool_label(pool)}。"
        if pool == "api":
            return head + f"请改用 {_pool_label(other)} / Composer，或等到恢复后再用 {_pool_label(pool)}。"
        return head + "请改用 Auto 或 API 模型，或等到恢复后再用合计额度。"
    return head


def _ok_payload(*, reason: str | None = None) -> dict:
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


def _stored_pools_for(pool: SpendPool) -> tuple[str, ...]:
    if pool == "total":
        return ("auto", "api")
    return (pool,)


def _null_row_matches_pool(model: str | None, pool: SpendPool) -> bool:
    effective = loan_usage_cap_pool(model)
    if effective is None:
        return False
    if pool == "total":
        return True
    return effective == pool


def _prefetch_null_pool_rows(
    session: Session,
    *,
    member_id: str | None,
    loan_id: str | None,
    cutoff: datetime,
) -> list[tuple[datetime, str | None, int]]:
    rows = session.execute(
        select(ProxyKeyUsage.ts, ProxyKeyUsage.model, ProxyKeyUsage.cost_cents)
        .where(
            _scope_filter(member_id, loan_id),
            ProxyKeyUsage.ts > cutoff,
            ProxyKeyUsage.usage_cap_pool.is_(None),
        )
        .order_by(ProxyKeyUsage.ts.asc())
    ).all()
    out: list[tuple[datetime, str | None, int]] = []
    for ts, model, cents in rows:
        ts_aware = ensure_aware(ts) or ts
        if ts_aware <= cutoff:
            continue
        out.append((ts_aware, model, int(cents or 0)))
    return out


def _sum_window_cents_fast(
    session: Session,
    *,
    member_id: str | None,
    loan_id: str | None,
    cutoff: datetime,
    pool: SpendPool,
    null_rows: list[tuple[datetime, str | None, int]],
) -> int:
    stored = _stored_pools_for(pool)
    sql_sum = session.scalar(
        select(func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0)).where(
            _scope_filter(member_id, loan_id),
            ProxyKeyUsage.ts > cutoff,
            ProxyKeyUsage.usage_cap_pool.in_(stored),
        )
    )
    total = int(sql_sum or 0)
    for ts, model, cents in null_rows:
        if ts <= cutoff:
            continue
        if _null_row_matches_pool(model, pool):
            total += cents
    return total


def _window_events_fast(
    session: Session,
    *,
    member_id: str | None,
    loan_id: str | None,
    cutoff: datetime,
    pool: SpendPool,
    null_rows: list[tuple[datetime, str | None, int]],
) -> list[tuple[datetime, int]]:
    stored = _stored_pools_for(pool)
    rows = session.execute(
        select(ProxyKeyUsage.ts, ProxyKeyUsage.cost_cents)
        .where(
            _scope_filter(member_id, loan_id),
            ProxyKeyUsage.ts > cutoff,
            ProxyKeyUsage.usage_cap_pool.in_(stored),
        )
        .order_by(ProxyKeyUsage.ts.asc())
    ).all()
    events: list[tuple[datetime, int]] = []
    for ts, cents in rows:
        ts_aware = ensure_aware(ts) or ts
        if ts_aware <= cutoff:
            continue
        events.append((ts_aware, int(cents or 0)))
    for ts, model, cents in null_rows:
        if ts <= cutoff:
            continue
        if _null_row_matches_pool(model, pool):
            events.append((ts, cents))
    events.sort(key=lambda item: item[0])
    return events


def rule_snapshots(
    session: Session,
    rules: list[SpendRule],
    *,
    member_id: str | None = None,
    loan_id: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Current-window usage snapshot per rule (member- or loan-scoped)."""
    from pulse.proxy.clock import utcnow
    from pulse.proxy.key_crud import cents_to_usd

    now = ensure_aware(now or utcnow()) or utcnow()
    if not rules:
        return []

    min_cutoff = now
    packed: list[tuple[SpendRule, timedelta, datetime]] = []
    for rule in rules:
        window = cap_window_for_period(rule.period)
        cutoff = _window_cutoff(now, window)
        packed.append((rule, window, cutoff))
        if cutoff < min_cutoff:
            min_cutoff = cutoff

    null_rows = _prefetch_null_pool_rows(session, member_id=member_id, loan_id=loan_id, cutoff=min_cutoff)
    snapshots: list[dict] = []
    for rule, window, cutoff in packed:
        events = _window_events_fast(
            session,
            member_id=member_id,
            loan_id=loan_id,
            cutoff=cutoff,
            pool=rule.pool,
            null_rows=null_rows,
        )
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
    return snapshots


def check_spend_rules(
    session: Session,
    rules: list[SpendRule],
    model: str | None,
    *,
    member_id: str | None = None,
    loan_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = ensure_aware(now or utcnow()) or utcnow()
    if not rules:
        return _ok_payload(reason="cap_disabled")

    request_pool = loan_usage_cap_pool(model)
    if request_pool is None:
        return _ok_payload(reason="not_counted")

    pools_to_check: list[SpendPool] = [request_pool]
    if any(rule.pool == "total" for rule in rules):
        pools_to_check.append("total")

    min_cutoff = now
    for rule in rules:
        cutoff = _window_cutoff(now, cap_window_for_period(rule.period))
        if cutoff < min_cutoff:
            min_cutoff = cutoff
    null_rows = _prefetch_null_pool_rows(session, member_id=member_id, loan_id=loan_id, cutoff=min_cutoff)
    used_cache: dict[tuple[str, SpendPool], int] = {}

    def used_for(period: str, pool: SpendPool) -> int:
        key = (period, pool)
        if key not in used_cache:
            cutoff = _window_cutoff(now, cap_window_for_period(period))
            used_cache[key] = _sum_window_cents_fast(
                session,
                member_id=member_id,
                loan_id=loan_id,
                cutoff=cutoff,
                pool=pool,
                null_rows=null_rows,
            )
        return used_cache[key]

    def exceeded_for(pool: SpendPool) -> list[tuple[SpendRule, int, datetime]]:
        hit: list[tuple[SpendRule, int, datetime]] = []
        for rule in rules:
            if rule.pool != pool:
                continue
            window = cap_window_for_period(rule.period)
            cutoff = _window_cutoff(now, window)
            used = used_for(rule.period, pool)
            if used < rule.limit_cents:
                continue
            events = _window_events_fast(
                session,
                member_id=member_id,
                loan_id=loan_id,
                cutoff=cutoff,
                pool=pool,
                null_rows=null_rows,
            )
            if not events:
                continue
            hit.append((rule, used, usage_resets_at(events, rule.limit_cents, window)))
        return hit

    exceeded: list[tuple[SpendRule, int, datetime]] = []
    for pool in pools_to_check:
        exceeded.extend(exceeded_for(pool))
    if not exceeded:
        return _ok_payload(reason=None)

    if any(item[0].pool == request_pool for item in exceeded):
        primary_pool: SpendPool = request_pool
        pool_exceeded = [item for item in exceeded if item[0].pool == request_pool]
    else:
        primary_pool = "total"
        pool_exceeded = [item for item in exceeded if item[0].pool == "total"]

    other = _other_pool(request_pool)
    other_open = not exceeded_for(other)
    if not any(rule.pool == other for rule in rules):
        other_open = True

    resets = max(item[2] for item in pool_exceeded)
    primary = max(pool_exceeded, key=lambda item: item[2])
    message = _build_limited_message(primary_pool, pool_exceeded, other_open=other_open, now=now)
    return {
        "status": "limited",
        "reason": "spend_rule_exceeded",
        "pool": primary_pool,
        "period": primary[0].period,
        "used_cents": primary[1],
        "limit_cents": primary[0].limit_cents,
        "resets_at": resets.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "other_pool": other,
        "other_pool_open": other_open,
        "message": message,
    }
