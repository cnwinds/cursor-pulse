"""Team-wide Cursor token usage analytics (calendar range, not billing cycle)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from pulse.pricing.billing_scope import pool_for_row
from pulse.storage.models import AiAccount, AiVendor, Member, UsageDailyAggregate
from pulse.tool_center.usage import model_family

_MAX_RANGE_DAYS = 366

POOL_LABELS = {
    "auto_composer": "Auto+Composer",
    "api": "API",
    "external": "三方/BYOK",
    "third_party": "三方/BYOK",  # legacy analytics key
    "excluded": "未计费",
}


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


def _row_kind_family(row: UsageDailyAggregate) -> str:
    return (getattr(row, "kind_family", None) or "").strip() or "unknown"


def _round_metrics(bucket: dict) -> dict:
    return {
        "tokens_input": int(bucket["tokens_input"]),
        "tokens_output": int(bucket["tokens_output"]),
        "tokens_cache_read": int(bucket["tokens_cache_read"]),
        "tokens_total": int(bucket["tokens_total"]),
        "event_count": int(bucket["event_count"]),
        "cost_usd": round(float(bucket["cost_usd"]), 4),
    }


def _cursor_aggregate_filters(
    team_id: str,
    *,
    start: date,
    end: date,
    account_ids: list[str] | None,
    primary_member_ids: list[str] | None,
):
    clauses = [
        AiAccount.team_id == team_id,
        AiAccount.deleted_at.is_(None),
        AiVendor.slug == "cursor",
        UsageDailyAggregate.event_date >= start,
        UsageDailyAggregate.event_date <= end,
    ]
    if account_ids:
        clauses.append(AiAccount.id.in_(account_ids))
    if primary_member_ids:
        clauses.append(AiAccount.primary_member_id.in_(primary_member_ids))
    return clauses


def build_usage_kpi_and_series(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    timezone: str,
    account_ids: list[str] | None = None,
    primary_member_ids: list[str] | None = None,
) -> dict:
    """SQL-aggregated KPI + daily series (no per-account/model breakdowns)."""
    validate_range(start, end)
    stmt = (
        select(
            UsageDailyAggregate.event_date,
            func.coalesce(func.sum(UsageDailyAggregate.tokens_input), 0),
            func.coalesce(func.sum(UsageDailyAggregate.tokens_output), 0),
            func.coalesce(func.sum(UsageDailyAggregate.tokens_cache_read), 0),
            func.coalesce(func.sum(UsageDailyAggregate.event_count), 0),
            func.coalesce(func.sum(UsageDailyAggregate.total_cost_usd), 0),
        )
        .select_from(UsageDailyAggregate)
        .join(AiAccount, UsageDailyAggregate.account_id == AiAccount.id)
        .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
        .where(
            *_cursor_aggregate_filters(
                team_id,
                start=start,
                end=end,
                account_ids=account_ids,
                primary_member_ids=primary_member_ids,
            )
        )
        .group_by(UsageDailyAggregate.event_date)
    )
    kpi = _metric_bucket()
    by_day: dict[date, dict] = {}
    for event_date, ti, to, tcr, events, cost in session.execute(stmt):
        bucket = {
            "tokens_input": int(ti or 0),
            "tokens_output": int(to or 0),
            "tokens_cache_read": int(tcr or 0),
            "tokens_total": 0,
            "event_count": int(events or 0),
            "cost_usd": float(cost or 0),
        }
        bucket["tokens_total"] = tokens_total_parts(
            bucket["tokens_input"], bucket["tokens_output"], bucket["tokens_cache_read"]
        )
        by_day[event_date] = bucket
        kpi["tokens_input"] += bucket["tokens_input"]
        kpi["tokens_output"] += bucket["tokens_output"]
        kpi["tokens_cache_read"] += bucket["tokens_cache_read"]
        kpi["tokens_total"] += bucket["tokens_total"]
        kpi["event_count"] += bucket["event_count"]
        kpi["cost_usd"] += bucket["cost_usd"]

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
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": timezone,
        "kpi": _round_metrics(kpi),
        "series_by_day": series_by_day,
    }


def _sum_metric_columns():
    return (
        func.coalesce(func.sum(UsageDailyAggregate.tokens_input), 0),
        func.coalesce(func.sum(UsageDailyAggregate.tokens_output), 0),
        func.coalesce(func.sum(UsageDailyAggregate.tokens_cache_read), 0),
        func.coalesce(func.sum(UsageDailyAggregate.event_count), 0),
        func.coalesce(func.sum(UsageDailyAggregate.total_cost_usd), 0),
    )


def _bucket_from_sums(ti, to, tcr, events, cost) -> dict:
    bucket = {
        "tokens_input": int(ti or 0),
        "tokens_output": int(to or 0),
        "tokens_cache_read": int(tcr or 0),
        "tokens_total": 0,
        "event_count": int(events or 0),
        "cost_usd": float(cost or 0),
    }
    bucket["tokens_total"] = tokens_total_parts(
        bucket["tokens_input"], bucket["tokens_output"], bucket["tokens_cache_read"]
    )
    return bucket


def _grouped_account_rows(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    account_ids: list[str] | None,
    primary_member_ids: list[str] | None,
) -> list[dict]:
    stmt = (
        select(
            AiAccount.id,
            AiAccount.account_identifier,
            AiAccount.primary_member_id,
            *_sum_metric_columns(),
        )
        .select_from(UsageDailyAggregate)
        .join(AiAccount, UsageDailyAggregate.account_id == AiAccount.id)
        .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
        .where(
            *_cursor_aggregate_filters(
                team_id,
                start=start,
                end=end,
                account_ids=account_ids,
                primary_member_ids=primary_member_ids,
            )
        )
        .group_by(AiAccount.id, AiAccount.account_identifier, AiAccount.primary_member_id)
    )
    raw_rows = list(session.execute(stmt))
    primary_ids = {row[2] for row in raw_rows if row[2]}
    member_names: dict[str, str] = {}
    if primary_ids:
        member_names = {
            m.id: m.display_name
            for m in session.scalars(select(Member).where(Member.id.in_(primary_ids)))
        }
    account_rows = []
    for account_id, identifier, primary_id, ti, to, tcr, events, cost in raw_rows:
        metrics = _round_metrics(_bucket_from_sums(ti, to, tcr, events, cost))
        account_rows.append(
            {
                "account_id": account_id,
                "account_identifier": identifier or "",
                "primary_member_name": member_names.get(primary_id) if primary_id else None,
                **metrics,
            }
        )
    return sorted(account_rows, key=lambda x: (-x["tokens_total"], x["account_identifier"]))


def _grouped_model_rows(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    account_ids: list[str] | None,
    primary_member_ids: list[str] | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    stmt = (
        select(
            UsageDailyAggregate.model,
            UsageDailyAggregate.kind_family,
            *_sum_metric_columns(),
        )
        .select_from(UsageDailyAggregate)
        .join(AiAccount, UsageDailyAggregate.account_id == AiAccount.id)
        .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
        .where(
            *_cursor_aggregate_filters(
                team_id,
                start=start,
                end=end,
                account_ids=account_ids,
                primary_member_ids=primary_member_ids,
            )
        )
        .group_by(UsageDailyAggregate.model, UsageDailyAggregate.kind_family)
    )
    by_model: dict[str, dict] = {}
    by_pool: dict[str, dict] = defaultdict(_metric_bucket)
    by_family: dict[str, dict] = defaultdict(_metric_bucket)
    for raw_model, raw_kind, ti, to, tcr, events, cost in session.execute(stmt):
        model = (raw_model or "unknown").strip() or "unknown"
        kind = (raw_kind or "").strip() or "unknown"
        bucket = _bucket_from_sums(ti, to, tcr, events, cost)
        model_key = f"{model}\0{kind}"
        existing = by_model.get(model_key)
        if existing is None:
            existing = {
                **_metric_bucket(),
                "model": model,
                "kind_family": kind,
                "pool": pool_for_row(model, kind),
                "family": model_family(model),
            }
            by_model[model_key] = existing
        existing["tokens_input"] += bucket["tokens_input"]
        existing["tokens_output"] += bucket["tokens_output"]
        existing["tokens_cache_read"] += bucket["tokens_cache_read"]
        existing["tokens_total"] += bucket["tokens_total"]
        existing["event_count"] += bucket["event_count"]
        existing["cost_usd"] += bucket["cost_usd"]
        _merge_metrics(by_pool[existing["pool"]], bucket)
        _merge_metrics(by_family[existing["family"]], bucket)

    model_rows = [
        {
            "model": bucket["model"],
            "kind_family": bucket["kind_family"],
            "pool": bucket["pool"],
            "family": bucket["family"],
            **_round_metrics(bucket),
        }
        for bucket in by_model.values()
    ]
    pool_rows = [
        {"pool": pool, "pool_label": POOL_LABELS.get(pool, pool), **_round_metrics(bucket)}
        for pool, bucket in by_pool.items()
    ]
    family_rows = [
        {"family": family, **_round_metrics(bucket)} for family, bucket in by_family.items()
    ]
    return (
        sorted(model_rows, key=lambda x: (-x["tokens_total"], x["model"], x["kind_family"])),
        sorted(pool_rows, key=lambda x: (-x["tokens_total"], x["pool"])),
        sorted(family_rows, key=lambda x: (-x["tokens_total"], x["family"])),
    )


def _merge_metrics(target: dict, source: dict) -> None:
    target["tokens_input"] += source["tokens_input"]
    target["tokens_output"] += source["tokens_output"]
    target["tokens_cache_read"] += source["tokens_cache_read"]
    target["tokens_total"] += source["tokens_total"]
    target["event_count"] += source["event_count"]
    target["cost_usd"] += source["cost_usd"]


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
    """SQL-grouped overview: KPI/series + account/model/pool/family rollups."""
    top_n = max(1, min(int(top_n), 50))
    light = build_usage_kpi_and_series(
        session,
        team_id,
        start=start,
        end=end,
        timezone=timezone,
        account_ids=account_ids,
        primary_member_ids=primary_member_ids,
    )
    account_rows = _grouped_account_rows(
        session,
        team_id,
        start=start,
        end=end,
        account_ids=account_ids,
        primary_member_ids=primary_member_ids,
    )
    model_rows, pool_rows, family_rows = _grouped_model_rows(
        session,
        team_id,
        start=start,
        end=end,
        account_ids=account_ids,
        primary_member_ids=primary_member_ids,
    )
    return {
        "start": light["start"],
        "end": light["end"],
        "timezone": light["timezone"],
        "top_n": top_n,
        "kpi": light["kpi"],
        "series_by_day": light["series_by_day"],
        "by_account": account_rows,
        "by_model": model_rows,
        "by_pool": pool_rows,
        "by_family": family_rows,
        "note": "用量池优先按 kind_family（INCLUDED vs USER_API_KEY）；无 kind 时按模型名近似。external = 三方/BYOK，不是 Cursor Quota Pool",
    }


def build_usage_analytics_daily_breakdown(
    session: Session,
    team_id: str,
    *,
    start: date,
    end: date,
    account_id: str | None = None,
    model: str | None = None,
    kind_family: str | None = None,
) -> list[dict]:
    validate_range(start, end)
    account_ids = [account_id] if account_id else None
    stmt = (
        select(UsageDailyAggregate, AiAccount)
        .join(AiAccount, UsageDailyAggregate.account_id == AiAccount.id)
        .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
        .where(
            *_cursor_aggregate_filters(
                team_id,
                start=start,
                end=end,
                account_ids=account_ids,
                primary_member_ids=None,
            )
        )
    )
    model_filter = (model or "").strip() or None
    kind_filter = (kind_family or "").strip() or None
    if model_filter:
        stmt = stmt.where(UsageDailyAggregate.model == model_filter)
    if kind_filter == "unknown":
        stmt = stmt.where(
            or_(
                UsageDailyAggregate.kind_family.is_(None),
                UsageDailyAggregate.kind_family == "",
                UsageDailyAggregate.kind_family == "unknown",
            )
        )
    elif kind_filter:
        stmt = stmt.where(UsageDailyAggregate.kind_family == kind_filter)
    out: list[dict] = []
    for row, account in session.execute(stmt):
        kind = _row_kind_family(row)
        ti = int(row.tokens_input or 0)
        to = int(row.tokens_output or 0)
        tcr = int(row.tokens_cache_read or 0)
        out.append(
            {
                "date": row.event_date.isoformat(),
                "account_id": account.id,
                "account_identifier": account.account_identifier or "",
                "model": row.model,
                "kind_family": kind,
                "pool": pool_for_row(row.model, kind),
                "family": model_family(row.model),
                "tokens_input": ti,
                "tokens_output": to,
                "tokens_cache_read": tcr,
                "tokens_total": tokens_total_parts(ti, to, tcr),
                "cost_usd": round(float(row.total_cost_usd or 0), 4),
                "event_count": int(row.event_count or 0),
            }
        )
    out.sort(key=lambda x: (x["date"], -x["tokens_total"], x["model"], x["kind_family"]))
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
