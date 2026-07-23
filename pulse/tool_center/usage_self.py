from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.periods import current_period
from pulse.proxy.service import loan_proxy_usage_summary
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    KeyLoan,
    Member,
    UsageDailyAggregate,
    UsageIngestion,
    UsageRecord,
)
from pulse.tool_center.billing_cycle import (
    add_months,
    billing_cycle_containing,
    format_cycle_range,
    period_first_day,
)
from pulse.tool_center.burn_rate import analyze_burn_rate
from pulse.util.datetime_fmt import format_china_datetime_iso, format_data_updated_line

_PERIOD_RE = re.compile(r"(20\d{2})-(0[1-9]|1[0-2])")


def parse_usage_period_request(text: str, *, default_period: str) -> tuple[str, str]:
    """返回 (mode, period)。mode: billing_cycle | calendar_month。"""
    m = _PERIOD_RE.search(text or "")
    if m:
        return "calendar_month", m.group(0)
    if any(k in (text or "") for k in ("自然月", "本月")):
        return "calendar_month", default_period
    return "billing_cycle", default_period


def is_self_usage_query(text: str) -> bool:
    t = (text or "").strip()
    if "用量" not in t:
        return False
    if any(k in t for k in ("谁", "排名", "最多")) and not any(k in t for k in ("我", "本人")):
        return False
    return any(k in t for k in ("我", "本人")) or t in ("用量",) or t.endswith("用量")


def resolve_account_window(
    *,
    mode: str,
    period: str,
    usage_resets_on: date | None,
    today: date,
) -> tuple[date, date, str]:
    """返回 [start, end) 与窗口说明标签。"""
    if mode == "calendar_month":
        start = period_first_day(period)
        end = add_months(start, 1)
        return start, end, f"自然月 {period}"

    if not usage_resets_on:
        start = period_first_day(period)
        end = add_months(start, 1)
        return start, end, f"自然月 {period}（无记账重置日，已回退）"

    start, end = billing_cycle_containing(today, usage_resets_on)
    return start, end, "记账周期"


def resolve_loan_usage_window(
    loan: KeyLoan,
    *,
    mode: str,
    period: str,
    usage_resets_on: date | None,
    today: date,
) -> tuple[date, date, str]:
    """借用期间与查询窗口的交集 [start, end)。"""
    loan_start = loan.created_at.date() if loan.created_at else today
    if mode == "calendar_month":
        win_start = period_first_day(period)
        win_end = add_months(win_start, 1)
        label = f"自然月 {period} · 借用段"
    elif usage_resets_on:
        win_start, win_end = billing_cycle_containing(today, usage_resets_on)
        label = "记账周期 · 借用段"
    else:
        win_start = period_first_day(period)
        win_end = add_months(win_start, 1)
        label = f"自然月 {period} · 借用段（无记账重置日，已回退）"

    start = max(loan_start, win_start)
    end = min(win_end, today + timedelta(days=1))
    if start >= end:
        start = loan_start
        end = min(win_end, loan_start + timedelta(days=1))
    return start, end, label


def _loan_borrowed_quota_pct(loan: KeyLoan, snapshot: AccountQuotaSnapshot) -> float | None:
    """借用期间消耗的额度，占借出账号当前周期总配额的比例（百分点）。"""
    borrowed_cents = max(snapshot.used_cents - loan.baseline_used_cents, 0)
    if snapshot.total_pct is not None and snapshot.used_cents > 0:
        baseline_total_pct = (loan.baseline_used_cents / snapshot.used_cents) * snapshot.total_pct
        return round(max(snapshot.total_pct - baseline_total_pct, 0.0), 1)
    if snapshot.limit_cents > 0:
        return round(borrowed_cents / snapshot.limit_cents * 100.0, 1)
    return None


def _date_window_to_utc_datetimes(start: date, end: date) -> tuple[datetime, datetime]:
    """Convert [start, end) calendar dates to UTC datetimes for ProxyKeyUsage.ts filters."""
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end, time.min, tzinfo=timezone.utc),
    )


def build_loan_usage_payload(
    session: Session,
    loan: KeyLoan,
    loan_svc: Any,
    *,
    mode: str,
    period: str,
    today: date,
) -> dict[str, Any] | None:
    account = session.get(AiAccount, loan.source_account_id)
    if account is None:
        return None

    lender_name: str | None = None
    if account.primary_member_id:
        lender = session.get(Member, account.primary_member_id)
        lender_name = lender.display_name if lender else None

    approx_cents = loan_svc.approximate_borrowed_cents(loan)
    snapshot = loan_svc.latest_snapshot(account.id)
    borrowed_quota_pct: float | None = None
    remaining_headroom_pct: float | None = None
    quota_captured_at: datetime | None = None
    if snapshot is not None:
        borrowed_quota_pct = _loan_borrowed_quota_pct(loan, snapshot)
        remaining_headroom_pct = analyze_burn_rate(snapshot, today).remaining_headroom_pct
        quota_captured_at = snapshot.captured_at

    win_start, win_end, window_label = resolve_loan_usage_window(
        loan,
        mode=mode,
        period=period,
        usage_resets_on=account.usage_resets_on,
        today=today,
    )
    ts_start, ts_end = _date_window_to_utc_datetimes(win_start, win_end)
    proxy = loan_proxy_usage_summary(
        session, loan.id, start=ts_start, end=ts_end
    )
    has_proxy = int(proxy.get("request_count") or 0) > 0
    usage_source = "proxy" if has_proxy else "quota_approx"

    return {
        "identifier": "借用 Key",
        "is_loan": True,
        "loan_id": loan.id,
        "source_identifier": account.account_identifier,
        "lender_name": lender_name,
        "loan_created_at": loan.created_at,
        "approx_borrowed_usd": approx_cents / 100.0,
        "borrowed_quota_pct": borrowed_quota_pct,
        "remaining_headroom_pct": remaining_headroom_pct,
        "quota_captured_at": quota_captured_at,
        "usage_source": usage_source,
        "window_label": window_label,
        "range_text": format_cycle_range(win_start, win_end),
        "events": int(proxy["request_count"]) if has_proxy else 0,
        "tokens": int(proxy["total_tokens"]) if has_proxy else 0,
        "cost_usd": float(proxy["cost_usd"]) if has_proxy else 0.0,
        "models": list(proxy["models"]) if has_proxy else [],
        "proxy_data_updated_at": proxy.get("data_updated_at") if has_proxy else None,
    }


def aggregate_models_from_daily_rows(rows: list[UsageDailyAggregate]) -> list[dict[str, Any]]:
    by_model: dict[str, dict[str, Any]] = {}
    for r in rows:
        bucket = by_model.setdefault(
            r.model,
            {"model": r.model, "events": 0, "tokens": 0, "cost_usd": 0.0},
        )
        bucket["events"] += int(r.event_count or 0)
        bucket["tokens"] += (
            int(r.tokens_input or 0)
            + int(r.tokens_output or 0)
            + int(r.tokens_cache_read or 0)
        )
        bucket["cost_usd"] += float(r.total_cost_usd or 0)
    return sorted(by_model.values(), key=lambda x: (-x["cost_usd"], -x["events"], x["model"]))


def load_account_model_usage(
    session: Session,
    *,
    account_id: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], datetime | None]:
    rows = list(
        session.scalars(
            select(UsageDailyAggregate).where(
                UsageDailyAggregate.account_id == account_id,
                UsageDailyAggregate.event_date >= start,
                UsageDailyAggregate.event_date < end,
            )
        ).all()
    )
    if rows:
        updated_at = max(
            (row.updated_at for row in rows if row.updated_at is not None),
            default=None,
        )
        return aggregate_models_from_daily_rows(rows), updated_at

    rec_rows = list(
        session.execute(
            select(UsageRecord, UsageIngestion.ingested_at)
            .join(UsageIngestion, UsageRecord.ingestion_id == UsageIngestion.id)
            .where(
                UsageIngestion.account_id == account_id,
                UsageIngestion.status == "confirmed",
                UsageRecord.event_date >= start,
                UsageRecord.event_date < end,
            )
        ).all()
    )
    by_model: dict[str, dict[str, Any]] = {}
    latest_ingested_at: datetime | None = None
    for rec, ingested_at in rec_rows:
        bucket = by_model.setdefault(
            rec.model,
            {"model": rec.model, "events": 0, "tokens": 0, "cost_usd": 0.0},
        )
        bucket["events"] += 1
        bucket["tokens"] += int(rec.tokens_total or 0)
        bucket["cost_usd"] += float(rec.cost_usd or 0)
        if ingested_at is not None and (
            latest_ingested_at is None or ingested_at > latest_ingested_at
        ):
            latest_ingested_at = ingested_at
    models = sorted(by_model.values(), key=lambda x: (-x["cost_usd"], -x["events"], x["model"]))
    return models, latest_ingested_at


def _fmt_int(value: int) -> str:
    return f"{int(value):,}"


def _fmt_cost(value: float, *, estimated: bool = False) -> str:
    prefix = "≈" if estimated else ""
    return f"{prefix}${float(value):.2f}"


def _model_share_pct(model: dict[str, Any], *, total_tokens: int, total_events: int) -> float:
    if total_tokens > 0:
        return int(model["tokens"]) / total_tokens * 100.0
    if total_events > 0:
        return int(model["events"]) / total_events * 100.0
    return 0.0


def _format_model_table(
    models: list[dict[str, Any]], *, estimated_cost: bool = False
) -> list[str]:
    if not models:
        return ["*暂无已上报明细*"]
    total_tokens = sum(int(m["tokens"]) for m in models)
    total_events = sum(int(m["events"]) for m in models)
    cost_header = "估算费用" if estimated_cost else "费用"
    lines = [
        f"| 模型 | 次数 | Tokens | {cost_header} | 占比 |",
        "| :--- | ---: | ---: | ---: | ---: |",
    ]
    for m in models:
        share = _model_share_pct(m, total_tokens=total_tokens, total_events=total_events)
        lines.append(
            f"| {m['model']} | {_fmt_int(m['events'])} | {_fmt_int(m['tokens'])} | "
            f"{_fmt_cost(m['cost_usd'], estimated=estimated_cost)} | {share:.1f}% |"
        )
    return lines


def _format_loan_section(acc: dict[str, Any]) -> list[str]:
    loan = acc.get("loan") if isinstance(acc.get("loan"), dict) else {}
    created = acc.get("loan_created_at") or loan.get("loan_created_at")
    title = "**借用 Key**"
    if isinstance(created, datetime):
        title += f"（{created.month}/{created.day} 起）"
    elif isinstance(created, str) and created.strip():
        try:
            dt = datetime.fromisoformat(created.strip().replace("Z", "+00:00"))
            title += f"（{dt.month}/{dt.day} 起）"
        except ValueError:
            pass
    lines = [title]

    usage_source = acc.get("usage_source") or loan.get("usage_source") or "quota_approx"
    if usage_source == "proxy":
        lines.append("来源：Proxy 精确计量 · 费用为本地估算")
        events = int(acc.get("events") or 0)
        tokens = int(acc.get("tokens") or 0)
        cost_usd = float(acc.get("cost_usd") or 0.0)
        lines.append(
            f"合计：{_fmt_int(events)} 次 · {_fmt_int(tokens)} tokens · "
            f"{_fmt_cost(cost_usd, estimated=True)}"
        )
        headroom = acc.get("remaining_headroom_pct")
        if headroom is None:
            headroom = loan.get("remaining_headroom_pct")
        if headroom is not None:
            lines.append(f"还能用：**{headroom:.1f}%**（借出账号额度余量）")
        else:
            lines.append("还能用：暂无快照")
        updated = (
            acc.get("proxy_data_updated_at")
            or acc.get("data_updated_at")
            or loan.get("proxy_data_updated_at")
        )
        if isinstance(updated, datetime):
            lines.append(format_data_updated_line(updated.isoformat()))
        elif isinstance(updated, str) and updated:
            lines.append(format_data_updated_line(updated))
        lines.append("")
        lines.extend(_format_model_table(acc.get("models") or [], estimated_cost=True))
        lines.append("")
        return lines

    consumed = acc.get("borrowed_quota_pct")
    if consumed is None:
        consumed = loan.get("borrowed_quota_pct")
    if consumed is not None:
        lines.append(f"- 已消耗：**{consumed:.1f}%**")
    else:
        lines.append("- 已消耗：暂无快照")
    headroom = acc.get("remaining_headroom_pct")
    if headroom is None:
        headroom = loan.get("remaining_headroom_pct")
    if headroom is not None:
        lines.append(f"- 还能用：**{headroom:.1f}%**")
    else:
        lines.append("- 还能用：暂无快照")
    captured = acc.get("quota_captured_at") or acc.get("data_updated_at")
    if isinstance(captured, datetime):
        lines.append(format_data_updated_line(captured.isoformat()))
    elif isinstance(captured, str) and captured:
        lines.append(format_data_updated_line(captured))
    lines.append("")
    return lines


def format_usage_self_message(
    *,
    mode: str,
    period: str,
    accounts: list[dict[str, Any]],
) -> str:
    if not accounts:
        return "尚未绑定 Cursor 账号"
    header = (
        f"### 你的用量（自然月 {period}）"
        if mode == "calendar_month"
        else "### 你的用量（当前账期）"
    )
    lines = [header, ""]
    for index, acc in enumerate(accounts):
        if index > 0:
            lines.append("---")
            lines.append("")
        if acc.get("is_loan") or acc.get("kind") == "loan":
            lines.extend(_format_loan_section(acc))
            continue
        lines.append(f"**{acc['identifier']}**  ")
        lines.append(f"周期：{acc['range_text']}（{acc['window_label']}）  ")
        lines.append(
            f"合计：{_fmt_int(acc['events'])} 次 · {_fmt_int(acc['tokens'])} tokens · {_fmt_cost(acc['cost_usd'])}"
        )
        lines.append(format_data_updated_line(acc.get("data_updated_at")))
        lines.append("")
        lines.extend(_format_model_table(acc.get("models") or []))
        lines.append("")
    lines.append("也可发送「额度」查看本人账号额度；借用中可发送「我的借用」查看 Key。")
    return "\n".join(lines).rstrip()


def _iso_or_none(value: Any) -> str | None:
    """Serialize datetimes for tool results as China local time (+08:00)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return format_china_datetime_iso(value)
    if isinstance(value, str) and value.strip():
        return format_china_datetime_iso(value.strip())
    return str(value)


def build_usage_self_payload(
    session: Session,
    *,
    accounts: list[Any],
    text: str,
    config: Any,
    today: date | None = None,
    member_id: str | None = None,
    team_id: str | None = None,
    encryption_key: str = "",
) -> dict[str, Any]:
    """Structured usage payload for Agent/Skill rendering (schema_version=1)."""
    from pulse.tool_center.account_pick import filter_cursor_accounts
    from pulse.tool_center.key_loans import KeyLoanService

    today = today or date.today()
    default_period = current_period(config)
    mode, period = parse_usage_period_request(text, default_period=default_period)
    cursor_accounts = filter_cursor_accounts(accounts)
    rows: list[dict[str, Any]] = []
    for account in cursor_accounts:
        start, end, label = resolve_account_window(
            mode=mode,
            period=period,
            usage_resets_on=account.usage_resets_on,
            today=today,
        )
        models, data_updated_at = load_account_model_usage(
            session, account_id=account.id, start=start, end=end
        )
        rows.append(
            {
                "kind": "owned",
                "identifier": account.account_identifier,
                "window_label": label,
                "range_text": format_cycle_range(start, end),
                "events": sum(m["events"] for m in models),
                "tokens": sum(m["tokens"] for m in models),
                "cost_usd": sum(m["cost_usd"] for m in models),
                "models": models,
                "data_updated_at": _iso_or_none(data_updated_at),
                "loan": None,
                # formatter / legacy compat
                "is_loan": False,
            }
        )

    if member_id:
        loan_svc = KeyLoanService(session, encryption_key or "")
        for loan in loan_svc.list_active_loans_for_borrower(member_id):
            loan_payload_item = build_loan_usage_payload(
                session,
                loan,
                loan_svc,
                mode=mode,
                period=period,
                today=today,
            )
            if loan_payload_item is None:
                continue
            usage_source = loan_payload_item.get("usage_source") or "quota_approx"
            data_updated = (
                loan_payload_item.get("proxy_data_updated_at")
                if usage_source == "proxy"
                else loan_payload_item.get("quota_captured_at")
            )
            rows.append(
                {
                    "kind": "loan",
                    "identifier": "借用 Key",
                    "window_label": loan_payload_item.get("window_label") or "借用",
                    "range_text": loan_payload_item.get("range_text") or "",
                    "events": int(loan_payload_item.get("events") or 0),
                    "tokens": int(loan_payload_item.get("tokens") or 0),
                    "cost_usd": float(loan_payload_item.get("cost_usd") or 0.0),
                    "models": list(loan_payload_item.get("models") or []),
                    "usage_source": usage_source,
                    "data_updated_at": _iso_or_none(data_updated),
                    "proxy_data_updated_at": _iso_or_none(
                        loan_payload_item.get("proxy_data_updated_at")
                    ),
                    "loan": {
                        "lender_name": loan_payload_item.get("lender_name"),
                        "source_identifier": loan_payload_item.get("source_identifier"),
                        "loan_created_at": _iso_or_none(
                            loan_payload_item.get("loan_created_at")
                        ),
                        "borrowed_quota_pct": loan_payload_item.get("borrowed_quota_pct"),
                        "remaining_headroom_pct": loan_payload_item.get(
                            "remaining_headroom_pct"
                        ),
                        "approx_borrowed_usd": loan_payload_item.get(
                            "approx_borrowed_usd"
                        ),
                        "usage_source": usage_source,
                    },
                    "is_loan": True,
                    "source_identifier": loan_payload_item.get("source_identifier"),
                    "lender_name": loan_payload_item.get("lender_name"),
                    "loan_created_at": _iso_or_none(
                        loan_payload_item.get("loan_created_at")
                    ),
                    "borrowed_quota_pct": loan_payload_item.get("borrowed_quota_pct"),
                    "remaining_headroom_pct": loan_payload_item.get(
                        "remaining_headroom_pct"
                    ),
                    "quota_captured_at": _iso_or_none(
                        loan_payload_item.get("quota_captured_at")
                    ),
                }
            )

    empty_reason = "no_cursor_or_loan" if not rows else None
    return {
        "schema_version": 1,
        "query": {"mode": mode, "period": period},
        "accounts": rows,
        "empty_reason": empty_reason,
    }


def build_usage_self_reply(
    session: Session,
    *,
    accounts: list[Any],
    text: str,
    config: Any,
    today: date | None = None,
    member_id: str | None = None,
    team_id: str | None = None,
    encryption_key: str = "",
) -> str:
    payload = build_usage_self_payload(
        session,
        accounts=accounts,
        text=text,
        config=config,
        today=today,
        member_id=member_id,
        team_id=team_id,
        encryption_key=encryption_key,
    )
    if payload.get("empty_reason") == "no_cursor_or_loan" or not payload.get("accounts"):
        return "尚未绑定 Cursor 账号，且当前无进行中的 Key 借用。"
    query = payload.get("query") or {}
    return format_usage_self_message(
        mode=str(query.get("mode") or "billing_cycle"),
        period=str(query.get("period") or ""),
        accounts=list(payload.get("accounts") or []),
    )
