"""Team-wide Cursor token usage analytics (calendar range, not billing cycle)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.pricing.billing_scope import (
    is_auto_composer_model,
    is_third_party_model,
)
from pulse.storage.models import AiAccount, AiVendor, Member, UsageDailyAggregate
from pulse.tool_center.usage import model_family

_MAX_RANGE_DAYS = 366

POOL_LABELS = {
    "auto_composer": "Auto+Composer",
    "api": "API",
    "third_party": "三方",
}


def pool_for_model(model: str | None) -> str:
    """Approximate pool from model name (daily agg has no kind)."""
    if is_auto_composer_model(model):
        return "auto_composer"
    if is_third_party_model(model):
        return "third_party"
    return "api"


def tokens_total_parts(tokens_input: int, tokens_output: int, tokens_cache_read: int) -> int:
    return int(tokens_input or 0) + int(tokens_output or 0) + int(tokens_cache_read or 0)


def parse_date_param(value: str, *, field: str) -> date:
    text = (value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 须为 YYYY-MM-DD") from exc


def validate_range(start: date, end: date) -> None:
    if end < start:
        raise ValueError("end 不能早于 start")
    if (end - start).days + 1 > _MAX_RANGE_DAYS:
        raise ValueError(f"查询区间不能超过 {_MAX_RANGE_DAYS} 天")


def _parse_id_list(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    items = [part.strip() for part in raw.split(",") if part.strip()]
    return items or None


def _metric_bucket() -> dict:
    return {
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_total": 0,
        "event_count": 0,
        "cost_usd": 0.0,
    }


def _add_row(bucket: dict, row: UsageDailyAggregate) -> None:
    ti = int(row.tokens_input or 0)
    to = int(row.tokens_output or 0)
    tcr = int(row.tokens_cache_read or 0)
    bucket["tokens_input"] += ti
    bucket["tokens_output"] += to
    bucket["tokens_cache_read"] += tcr
    bucket["tokens_total"] += tokens_total_parts(ti, to, tcr)
    bucket["event_count"] += int(row.event_count or 0)
    bucket["cost_usd"] += float(row.total_cost_usd or 0)


def _round_metrics(bucket: dict) -> dict:
    return {
        "tokens_input": int(bucket["tokens_input"]),
        "tokens_output": int(bucket["tokens_output"]),
        "tokens_cache_read": int(bucket["tokens_cache_read"]),
        "tokens_total": int(bucket["tokens_total"]),
        "event_count": int(bucket["event_count"]),
        "cost_usd": round(float(bucket["cost_usd"]), 4),
    }


def _load_cursor_rows(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    account_ids: list[str] | None,
    primary_member_ids: list[str] | None,
) -> list[tuple[UsageDailyAggregate, AiAccount]]:
    stmt = (
        select(UsageDailyAggregate, AiAccount)
        .join(AiAccount, UsageDailyAggregate.account_id == AiAccount.id)
        .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
        .where(
            AiAccount.team_id == team_id,
            AiAccount.deleted_at.is_(None),
            AiVendor.slug == "cursor",
            UsageDailyAggregate.event_date >= start,
            UsageDailyAggregate.event_date <= end,
        )
    )
    if account_ids:
        stmt = stmt.where(AiAccount.id.in_(account_ids))
    if primary_member_ids:
        stmt = stmt.where(AiAccount.primary_member_id.in_(primary_member_ids))
    return list(session.execute(stmt).all())


def build_usage_analytics_overview(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    timezone: str,
    account_ids: list[str] | None = None,
    primary_member_ids: list[str] | None = None,
    top_n: int = 10,
) -> dict:
    validate_range(start, end)
    top_n = max(1, min(int(top_n), 50))
    pairs = _load_cursor_rows(
        session,
        team_id,
        start=start,
        end=end,
        account_ids=account_ids,
        primary_member_ids=primary_member_ids,
    )

    primary_ids = {acc.primary_member_id for _, acc in pairs if acc.primary_member_id}
    member_names: dict[str, str] = {}
    if primary_ids:
        member_names = {
            m.id: m.display_name
            for m in session.scalars(select(Member).where(Member.id.in_(primary_ids)))
        }

    kpi = _metric_bucket()
    by_day: dict[date, dict] = defaultdict(_metric_bucket)
    by_account: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_pool: dict[str, dict] = defaultdict(_metric_bucket)
    by_family: dict[str, dict] = defaultdict(_metric_bucket)

    for row, account in pairs:
        _add_row(kpi, row)
        _add_row(by_day[row.event_date], row)

        acc_bucket = by_account.get(account.id)
        if acc_bucket is None:
            acc_bucket = {
                **_metric_bucket(),
                "account_id": account.id,
                "account_identifier": account.account_identifier or "",
                "primary_member_name": member_names.get(account.primary_member_id or "")
                if account.primary_member_id
                else None,
            }
            by_account[account.id] = acc_bucket
        _add_row(acc_bucket, row)

        model = (row.model or "unknown").strip() or "unknown"
        pool = pool_for_model(model)
        family = model_family(model)
        model_bucket = by_model.get(model)
        if model_bucket is None:
            model_bucket = {
                **_metric_bucket(),
                "model": model,
                "pool": pool,
                "family": family,
            }
            by_model[model] = model_bucket
        _add_row(model_bucket, row)
        _add_row(by_pool[pool], row)
        _add_row(by_family[family], row)

    # Fill missing days in range for a continuous series.
    series_by_day = []
    cursor = start
    while cursor <= end:
        series_by_day.append(
            {
                "date": cursor.isoformat(),
                **_round_metrics(by_day.get(cursor, _metric_bucket())),
            }
        )
        cursor += timedelta(days=1)

    account_rows = []
    for bucket in by_account.values():
        metrics = _round_metrics(bucket)
        account_rows.append(
            {
                "account_id": bucket["account_id"],
                "account_identifier": bucket["account_identifier"],
                "primary_member_name": bucket["primary_member_name"],
                **metrics,
            }
        )

    model_rows = []
    for bucket in by_model.values():
        metrics = _round_metrics(bucket)
        model_rows.append(
            {
                "model": bucket["model"],
                "pool": bucket["pool"],
                "family": bucket["family"],
                **metrics,
            }
        )

    pool_rows = [
        {"pool": pool, "pool_label": POOL_LABELS.get(pool, pool), **_round_metrics(bucket)}
        for pool, bucket in by_pool.items()
    ]
    family_rows = [
        {"family": family, **_round_metrics(bucket)} for family, bucket in by_family.items()
    ]

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": timezone,
        "top_n": top_n,
        "kpi": _round_metrics(kpi),
        "series_by_day": series_by_day,
        "by_account": sorted(
            account_rows, key=lambda x: (-x["tokens_total"], x["account_identifier"])
        ),
        "by_model": sorted(model_rows, key=lambda x: (-x["tokens_total"], x["model"])),
        "by_pool": sorted(pool_rows, key=lambda x: (-x["tokens_total"], x["pool"])),
        "by_family": sorted(family_rows, key=lambda x: (-x["tokens_total"], x["family"])),
        "note": "用量池按模型名归类（日聚合无 kind）",
    }


def build_usage_analytics_daily_breakdown(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    account_id: str | None = None,
    model: str | None = None,
) -> list[dict]:
    validate_range(start, end)
    account_ids = [account_id] if account_id else None
    pairs = _load_cursor_rows(
        session,
        team_id,
        start=start,
        end=end,
        account_ids=account_ids,
        primary_member_ids=None,
    )
    model_filter = (model or "").strip() or None
    out: list[dict] = []
    for row, account in pairs:
        if model_filter and (row.model or "") != model_filter:
            continue
        ti = int(row.tokens_input or 0)
        to = int(row.tokens_output or 0)
        tcr = int(row.tokens_cache_read or 0)
        out.append(
            {
                "date": row.event_date.isoformat(),
                "account_id": account.id,
                "account_identifier": account.account_identifier or "",
                "model": row.model,
                "pool": pool_for_model(row.model),
                "family": model_family(row.model),
                "tokens_input": ti,
                "tokens_output": to,
                "tokens_cache_read": tcr,
                "tokens_total": tokens_total_parts(ti, to, tcr),
                "cost_usd": round(float(row.total_cost_usd or 0), 4),
                "event_count": int(row.event_count or 0),
            }
        )
    out.sort(key=lambda x: (x["date"], -x["tokens_total"], x["model"]))
    return out


def parse_overview_filters(
    *,
    start: str,
    end: str,
    account_ids: str | None,
    primary_member_ids: str | None,
) -> tuple[date, date, list[str] | None, list[str] | None]:
    start_d = parse_date_param(start, field="start")
    end_d = parse_date_param(end, field="end")
    validate_range(start_d, end_d)
    return start_d, end_d, _parse_id_list(account_ids), _parse_id_list(primary_member_ids)
