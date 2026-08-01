from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.pricing.cursor_tables import get_cursor_pricing_table
from pulse.pricing.types import PricingTable, estimate_token_cost
from pulse.proxy.clock import WINDOW_5H, utcnow
from pulse.proxy.usage_queries import loan_proxy_totals
from pulse.storage.models import AiAccount, KeyLoan, Member, ProxyKey, ProxyKeyUsage

logger = logging.getLogger(__name__)

_utcnow = utcnow


def window_usage_cost(
    session: Session,
    proxy_key_id: str,
    *,
    window: timedelta,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    since = now - window
    value = session.execute(
        select(func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0)).where(
            ProxyKeyUsage.proxy_key_id == proxy_key_id,
            ProxyKeyUsage.ts >= since,
        )
    ).scalar_one()
    return int(value)


def window_usage_tokens(session: Session, proxy_key_id: str, *, now: datetime | None = None) -> int:
    """Legacy helper: 5h token sum (kept for callers; limits no longer use tokens)."""
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    since = now - WINDOW_5H
    # ProxyKeyUsage.ts 统一按 UTC 写入；SQLite 绑参时 tzinfo 被静默丢弃，比较基于 UTC 墙钟
    value = session.execute(
        select(func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0)).where(
            ProxyKeyUsage.proxy_key_id == proxy_key_id,
            ProxyKeyUsage.ts >= since,
        )
    ).scalar_one()
    return int(value)


def total_usage(session: Session, proxy_key_id: str) -> tuple[int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        ).where(ProxyKeyUsage.proxy_key_id == proxy_key_id)
    ).one()
    return int(row[0]), int(row[1])


def loan_proxy_usage_summary(
    session: Session,
    loan_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Aggregate ProxyKeyUsage for a loan, optionally filtered by [start, end).

    Returns request_count, total_tokens, cost_cents, cost_usd, models[], data_updated_at.
    models entries use events/tokens/cost_usd for bot usage formatting.
    """
    clauses = [ProxyKeyUsage.loan_id == loan_id]
    if start is not None:
        clauses.append(ProxyKeyUsage.ts >= start)
    if end is not None:
        clauses.append(ProxyKeyUsage.ts < end)

    rows = list(
        session.execute(
            select(ProxyKeyUsage).where(*clauses).order_by(ProxyKeyUsage.ts.desc())
        )
        .scalars()
        .all()
    )
    by_model: dict[str, dict] = {}
    total_tokens = 0
    cost_cents = 0
    data_updated_at: datetime | None = None
    for u in rows:
        label = (u.model or "").strip() or "（未知）"
        bucket = by_model.get(label)
        if bucket is None:
            bucket = {"model": label, "events": 0, "tokens": 0, "cost_usd": 0.0}
            by_model[label] = bucket
        bucket["events"] += 1
        tokens = int(u.total_tokens or 0)
        cents = int(u.cost_cents or 0)
        bucket["tokens"] += tokens
        bucket["cost_usd"] += cents / 100.0
        total_tokens += tokens
        cost_cents += cents
        if u.ts is not None and (data_updated_at is None or u.ts > data_updated_at):
            data_updated_at = u.ts

    models = sorted(
        by_model.values(),
        key=lambda r: (-r["cost_usd"], -r["events"], r["model"]),
    )
    return {
        "request_count": len(rows),
        "total_tokens": total_tokens,
        "cost_cents": cost_cents,
        "cost_usd": cost_cents / 100.0,
        "models": models,
        "data_updated_at": data_updated_at,
    }



_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning")


def _normalize_tokens(tokens: dict) -> dict:
    """None/负数/缺失统一归一为 >=0 的 int；字符串数字亦可被 int() 接受。"""
    return {name: max(0, int(tokens.get(name) or 0)) for name in _TOKEN_FIELDS}


def canonical_turn_ended_tokens(tokens: dict) -> dict:
    """把 Go TurnEnded 五元组规整为官方 Dashboard 口径。

    TurnEnded field1(Input) 经常是「含 cache 的 input 侧合计」
    （no_cache + cache_write + cache_read），不是 input_no_cache。
    若 input >= cache_read + cache_write 且 cache 非零，则拆出 no_cache；
    否则把 input 当作已经是 no_cache。
    """
    raw = _normalize_tokens(tokens)
    cache_read = raw["cache_read"]
    cache_write = raw["cache_write"]
    inclusive_floor = cache_read + cache_write
    input_raw = raw["input"]
    if inclusive_floor > 0 and input_raw >= inclusive_floor:
        no_cache = input_raw - inclusive_floor
    else:
        no_cache = input_raw
    return {
        "input": no_cache,
        "output": raw["output"],
        "cache_read": cache_read,
        "cache_write": cache_write,
        "reasoning": raw["reasoning"],
    }


def total_tokens_from_canonical(tokens: dict) -> int:
    """canonical 后的总量：与官方 tokens_total 对齐，另含 reasoning。"""
    t = _normalize_tokens(tokens)
    return (
        t["input"]
        + t["output"]
        + t["cache_read"]
        + t["cache_write"]
        + t["reasoning"]
    )


def estimate_cost_cents(
    model: str | None,
    tokens: dict,
    *,
    table: PricingTable | None = None,
) -> int:
    """本地价表估算（美分）。始终先 canonical，避免 raw inclusive input 双计。"""
    canonical = canonical_turn_ended_tokens(tokens)
    pricing = table or get_cursor_pricing_table()
    total = total_tokens_from_canonical(canonical)
    est = estimate_token_cost(
        model=model or "",
        max_mode=False,
        tokens_input_no_cache=canonical["input"],
        tokens_input_cache_write=canonical["cache_write"],
        tokens_cache_read=canonical["cache_read"],
        tokens_output=canonical["output"] + canonical["reasoning"],
        table=pricing,
    )
    if est is not None:
        cents = int(round(est.cost_usd * 100))
        if cents > 0 or total <= 0:
            return cents
        # Sub-cent priced usage still counts toward window limits.
        return 1
    return _conservative_cost_cents_from_tokens(canonical, pricing)


def _conservative_cost_cents_from_tokens(
    tokens: dict,
    table: PricingTable,
) -> int:
    """Fallback when no pricing rule matches — bill all tokens at fallback input rate."""
    total = total_tokens_from_canonical(tokens)
    if total <= 0:
        return 0
    rule = table.fallback
    if rule is not None:
        est = estimate_token_cost(
            model=rule.pattern,
            max_mode=False,
            tokens_input_no_cache=total,
            tokens_input_cache_write=0,
            tokens_cache_read=0,
            tokens_output=0,
            table=table,
            pricing_rule_label="fallback-conservative",
            confidence=0.5,
        )
        if est is not None and est.cost_usd > 0:
            return max(1, int(round(est.cost_usd * 100)))
    # Sonnet-class $3/M input when table has no fallback.
    return max(1, int(round(total / 1_000_000 * 3.0 * 100)))


def reprice_proxy_usages(
    session: Session,
    *,
    loan_id: str | None = None,
    proxy_key_id: str | None = None,
) -> dict:
    """按 canonical 口径回算 ProxyKeyUsage 的 tokens_input / total_tokens / cost_cents。

    用于修复 TurnEnded inclusive input 双计的历史行；对已正确行幂等（updated=0）。
    """
    query = select(ProxyKeyUsage)
    if loan_id:
        query = query.where(ProxyKeyUsage.loan_id == loan_id)
    if proxy_key_id:
        query = query.where(ProxyKeyUsage.proxy_key_id == proxy_key_id)
    rows = list(session.scalars(query))
    pricing_by_team: dict[str, PricingTable] = {}
    updated = 0
    for row in rows:
        raw = {
            "input": row.tokens_input or 0,
            "output": row.tokens_output or 0,
            "cache_read": row.tokens_cache_read or 0,
            "cache_write": row.tokens_cache_write or 0,
            "reasoning": row.tokens_reasoning or 0,
        }
        tokens = canonical_turn_ended_tokens(raw)
        total = total_tokens_from_canonical(tokens)
        table = _pricing_table_for_usage_row(session, row, pricing_by_team)
        cost = estimate_cost_cents(row.model, tokens, table=table)
        if (
            int(row.tokens_input or 0) == tokens["input"]
            and int(row.total_tokens or 0) == total
            and int(row.cost_cents or 0) == cost
        ):
            continue
        row.tokens_input = tokens["input"]
        row.tokens_output = tokens["output"]
        row.tokens_cache_read = tokens["cache_read"]
        row.tokens_cache_write = tokens["cache_write"]
        row.tokens_reasoning = tokens["reasoning"]
        row.total_tokens = total
        row.cost_cents = cost
        updated += 1
    session.flush()
    return {"scanned": len(rows), "updated": updated}


def _pricing_table_for_usage_row(
    session: Session,
    row: ProxyKeyUsage,
    pricing_by_team: dict[str, PricingTable],
) -> PricingTable | None:
    team_id: str | None = None
    if row.proxy_key_id:
        key = session.get(ProxyKey, row.proxy_key_id)
        if key and key.member_id:
            member = session.get(Member, key.member_id)
            if member:
                team_id = member.team_id
    elif row.loan_id:
        loan = session.get(KeyLoan, row.loan_id)
        if loan:
            account = session.get(AiAccount, loan.source_account_id)
            if account:
                team_id = account.team_id
    if not team_id:
        return None
    table = pricing_by_team.get(team_id)
    if table is None:
        table = get_cursor_pricing_table(session=session, team_id=team_id)
        pricing_by_team[team_id] = table
    return table


def record_usages(
    session: Session, items: list[dict], *, now: datetime | None = None
) -> dict:
    now = now or utcnow()
    recorded = 0
    touched: set[str] = set()
    pricing_by_team: dict[str, PricingTable] = {}
    for item in items:
        proxy_key_id = item.get("proxy_key_id") or None
        loan_id = item.get("loan_id") or None
        if bool(proxy_key_id) == bool(loan_id):
            continue
        request_id = item.get("request_id")
        tokens = canonical_turn_ended_tokens(item.get("tokens") or {})
        total = total_tokens_from_canonical(tokens)
        ts = item.get("ts")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
        if proxy_key_id:
            key = session.get(ProxyKey, proxy_key_id)
            if key is None:
                continue
            if request_id:
                dup = session.execute(
                    select(ProxyKeyUsage.id).where(
                        ProxyKeyUsage.proxy_key_id == key.id,
                        ProxyKeyUsage.request_id == request_id,
                    )
                ).first()
                if dup is not None:
                    continue
            table = None
            member = session.get(Member, key.member_id) if key.member_id else None
            if member and member.team_id:
                table = pricing_by_team.get(member.team_id)
                if table is None:
                    table = get_cursor_pricing_table(session=session, team_id=member.team_id)
                    pricing_by_team[member.team_id] = table
            session.add(
                ProxyKeyUsage(
                    proxy_key_id=key.id,
                    credential_id=item.get("credential_id"),
                    request_id=request_id,
                    model=item.get("model"),
                    tokens_input=tokens["input"],
                    tokens_output=tokens["output"],
                    tokens_cache_read=tokens["cache_read"],
                    tokens_cache_write=tokens["cache_write"],
                    tokens_reasoning=tokens["reasoning"],
                    total_tokens=total,
                    cost_cents=estimate_cost_cents(item.get("model"), tokens, table=table),
                    ts=ts or now,
                )
            )
            recorded += 1
            touched.add(key.id)
        else:
            loan = session.get(KeyLoan, loan_id)
            if loan is None:
                continue
            if request_id:
                dup = session.execute(
                    select(ProxyKeyUsage.id).where(
                        ProxyKeyUsage.loan_id == loan.id,
                        ProxyKeyUsage.request_id == request_id,
                    )
                ).first()
                if dup is not None:
                    continue
            table = None
            account = session.get(AiAccount, loan.source_account_id)
            if account and account.team_id:
                table = pricing_by_team.get(account.team_id)
                if table is None:
                    table = get_cursor_pricing_table(session=session, team_id=account.team_id)
                    pricing_by_team[account.team_id] = table
            session.add(
                ProxyKeyUsage(
                    proxy_key_id=None,
                    loan_id=loan.id,
                    credential_id=item.get("credential_id"),
                    request_id=request_id,
                    model=item.get("model"),
                    tokens_input=tokens["input"],
                    tokens_output=tokens["output"],
                    tokens_cache_read=tokens["cache_read"],
                    tokens_cache_write=tokens["cache_write"],
                    tokens_reasoning=tokens["reasoning"],
                    total_tokens=total,
                    cost_cents=estimate_cost_cents(item.get("model"), tokens, table=table),
                    ts=ts or now,
                )
            )
            recorded += 1
    session.flush()
    suspended: list[str] = []
    for key_id in sorted(touched):
        key = session.get(ProxyKey, key_id)
        if key is not None:
            from pulse.proxy.key_crud import evaluate_key

            if evaluate_key(session, key):
                suspended.append(key_id)
    return {"recorded": recorded, "suspended": suspended}


