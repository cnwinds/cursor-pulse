from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from pulse.storage.db import init_db
from pulse.storage.models import (
    AiAccountCredential,
    KeyLoan,
    ProxyKeyUsage,
    UsageDailyAggregate,
)
from pulse.tool_center.key_loans import KeyLoanService
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage_self import (
    _loan_borrowed_quota_pct,
    _model_share_pct,
    aggregate_models_from_daily_rows,
    build_loan_usage_payload,
    build_usage_self_payload,
    format_usage_self_message,
    is_self_usage_query,
    load_account_model_usage,
    parse_usage_period_request,
    resolve_account_window,
    resolve_loan_usage_window,
)
from tests.conftest import make_team_repo


def _daily_row(
    model: str,
    *,
    event_count: int,
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_cache_read: int = 0,
    total_cost_usd: float = 0.0,
):
    return SimpleNamespace(
        model=model,
        event_count=event_count,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cache_read=tokens_cache_read,
        total_cost_usd=total_cost_usd,
    )


def test_is_self_usage_query():
    # 含「我」的自助用量句式
    assert is_self_usage_query("查下我的用量") is True
    # 团队 NL：无「我/本人」且含排名类关键词 → False（且本句不含「用量」子串）
    assert is_self_usage_query("谁用得最多") is False
    # 仅「用量」二字：当前实现视为自助（t in ("用量",)）
    assert is_self_usage_query("用量") is True


def test_aggregate_models_from_daily_rows():
    rows = [
        _daily_row("cheap", event_count=10, tokens_input=10, total_cost_usd=0.5),
        _daily_row("cheap", event_count=5, tokens_output=20, total_cost_usd=0.3),
        _daily_row("expensive", event_count=2, tokens_input=100, total_cost_usd=5.0),
    ]
    result = aggregate_models_from_daily_rows(rows)
    assert [r["model"] for r in result] == ["expensive", "cheap"]
    cheap = result[1]
    assert cheap["events"] == 15
    assert cheap["tokens"] == 30
    assert cheap["cost_usd"] == pytest.approx(0.8)


def test_load_account_model_usage_daily_aggregate():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    try:
        team, _repo = make_team_repo(session)
        seed_v2_catalog(session, team)
        session.flush()
        tool_repo = ToolCenterRepository(session, team.id)
        account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
        session.add(
            UsageDailyAggregate(
                account_id=account.id,
                event_date=date(2026, 6, 15),
                model="composer-2",
                event_count=5,
                total_cost_usd=2.5,
                tokens_input=100,
                tokens_output=200,
                tokens_cache_read=50,
                updated_at=datetime(2026, 7, 15, 4, 30, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

        models, data_updated_at = load_account_model_usage(
            session,
            account_id=account.id,
            start=date(2026, 6, 1),
            end=date(2026, 7, 1),
        )
        assert len(models) == 1
        assert models[0]["model"] == "composer-2"
        assert models[0]["events"] == 5
        assert models[0]["tokens"] == 350
        assert models[0]["cost_usd"] == pytest.approx(2.5)
        assert data_updated_at is not None
        assert data_updated_at.hour == 4
    finally:
        session.close()


def test_parse_default_billing_cycle():
    mode, period = parse_usage_period_request("查下我的用量", default_period="2026-07")
    assert mode == "billing_cycle"
    assert period == "2026-07"


def test_parse_calendar_month_keyword():
    mode, period = parse_usage_period_request("查下我的用量 自然月", default_period="2026-07")
    assert mode == "calendar_month"
    assert period == "2026-07"


def test_parse_explicit_yyyy_mm():
    mode, period = parse_usage_period_request("我的用量 2026-06", default_period="2026-07")
    assert mode == "calendar_month"
    assert period == "2026-06"


def test_resolve_billing_cycle_per_account():
    start, end, label = resolve_account_window(
        mode="billing_cycle",
        period="2026-07",
        usage_resets_on=date(2026, 7, 15),
        today=date(2026, 7, 10),
    )
    assert start == date(2026, 6, 15)
    assert end == date(2026, 7, 15)
    assert "记账" in label or "billing" in label.lower() or label.startswith("周期")


def test_resolve_fallback_without_resets_on():
    start, end, label = resolve_account_window(
        mode="billing_cycle",
        period="2026-07",
        usage_resets_on=None,
        today=date(2026, 7, 10),
    )
    assert start == date(2026, 7, 1)
    assert end == date(2026, 8, 1)
    assert "自然月" in label


def test_model_share_pct_prefers_tokens_then_events():
    assert _model_share_pct(
        {"events": 3, "tokens": 75},
        total_tokens=100,
        total_events=10,
    ) == pytest.approx(75.0)
    assert _model_share_pct(
        {"events": 3, "tokens": 0},
        total_tokens=0,
        total_events=10,
    ) == pytest.approx(30.0)


def test_format_lists_all_models():
    msg = format_usage_self_message(
        mode="billing_cycle",
        period="2026-07",
        accounts=[
            {
                "identifier": "a@x.com",
                "window_label": "记账周期",
                "range_text": "2026-06-15 ~ 2026-07-14",
                "events": 10,
                "tokens": 100,
                "cost_usd": 1.5,
                "data_updated_at": datetime(2026, 7, 15, 4, 30, 0, tzinfo=timezone.utc),
                "models": [
                    {"model": "m1", "events": 6, "tokens": 60, "cost_usd": 1.0},
                    {"model": "m2", "events": 4, "tokens": 40, "cost_usd": 0.5},
                ],
            }
        ],
    )
    assert "a@x.com" in msg
    assert "m1" in msg and "m2" in msg
    assert "当前账期" in msg or "用量" in msg
    assert "| 模型 | 次数 | Tokens | 费用 | 占比 |" in msg
    assert "| m1 | 6 | 60 | $1.00 | 60.0% |" in msg
    assert "| m2 | 4 | 40 | $0.50 | 40.0% |" in msg
    assert "数据最后更新：2026-07-15 12:30:00" in msg
    assert "总览" not in msg
    assert "| 账号 |" not in msg


def _loan_created(created_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        created_at=created_at
        if created_at is not None
        else datetime(2026, 7, 27, 1, 56, 7)
    )


def test_resolve_loan_usage_window_billing_cycle_uses_full_loan_period():
    """Default「我的用量」：Proxy 按借用周期，不按借出账号账期截断。"""
    start, end, label = resolve_loan_usage_window(
        _loan_created(),
        mode="billing_cycle",
        period="2026-08",
        today=date(2026, 8, 4),
    )
    assert start == date(2026, 7, 27)
    assert end == date(2026, 8, 5)
    assert label == "借用周期"
    # 借出账号账期本会是 8/1~9/1；不得用其截掉 7 月用量
    assert start < date(2026, 8, 1)


def test_resolve_loan_usage_window_calendar_month_intersects_loan():
    loan = _loan_created()
    start, end, label = resolve_loan_usage_window(
        loan,
        mode="calendar_month",
        period="2026-08",
        today=date(2026, 8, 4),
    )
    assert start == date(2026, 8, 1)
    assert end == date(2026, 8, 5)
    assert label == "自然月 2026-08 · 借用段"

    start_jul, end_jul, label_jul = resolve_loan_usage_window(
        loan,
        mode="calendar_month",
        period="2026-07",
        today=date(2026, 8, 4),
    )
    assert start_jul == date(2026, 7, 27)
    assert end_jul == date(2026, 8, 1)
    assert label_jul == "自然月 2026-07 · 借用段"


def test_resolve_loan_usage_window_missing_created_at_falls_back_to_today():
    start, end, label = resolve_loan_usage_window(
        SimpleNamespace(created_at=None),
        mode="billing_cycle",
        period="2026-08",
        today=date(2026, 8, 4),
    )
    assert start == date(2026, 8, 4)
    assert end == date(2026, 8, 5)
    assert label == "借用周期"


def test_resolve_loan_usage_window_empty_calendar_month_falls_back():
    """指定尚未开始的自然月时缩到可展示的最小窗，不报错。"""
    start, end, label = resolve_loan_usage_window(
        _loan_created(),
        mode="calendar_month",
        period="2026-09",
        today=date(2026, 8, 4),
    )
    assert start == date(2026, 7, 27)
    assert end == date(2026, 7, 28)
    assert label == "自然月 2026-09 · 借用段"


def _seed_proxy_loan_with_july_usage(session):
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    account.usage_resets_on = date(2027, 2, 1)
    borrower = repo.add_member("borrower-lzj", "陆宗靖")
    session.flush()
    cred = AiAccountCredential(
        account_id=account.id,
        vendor_id=account.vendor_id,
        credential_type="cursor_api_key",
        encrypted_value="enc",
        key_hint="crsr_...test",
        key_role="loan",
        bound_by_member_id=borrower.id,
        assignee_member_id=borrower.id,
    )
    session.add(cred)
    session.flush()
    loan = KeyLoan(
        source_account_id=account.id,
        credential_id=cred.id,
        borrower_member_id=borrower.id,
        baseline_used_cents=3197,
        status="active",
        created_at=datetime(2026, 7, 27, 1, 56, 7),
    )
    session.add(loan)
    session.flush()
    session.add(
        ProxyKeyUsage(
            loan_id=loan.id,
            model="composer-2.5-fast",
            total_tokens=1_000_000,
            cost_cents=1453,
            ts=datetime(2026, 7, 29, 8, 0, 0),
        )
    )
    session.commit()
    return loan


def test_build_loan_usage_payload_proxy_spans_billing_month_rollover():
    """Regression: 7 月 Proxy 用量在 8 月问「我的用量」仍按借用周期可见。"""
    session = init_db("sqlite:///:memory:")()
    try:
        loan = _seed_proxy_loan_with_july_usage(session)
        loan_svc = KeyLoanService(session, "")
        payload = build_loan_usage_payload(
            session,
            loan,
            loan_svc,
            mode="billing_cycle",
            period="2026-08",
            today=date(2026, 8, 4),
        )
        assert payload is not None
        assert payload["usage_source"] == "proxy"
        assert payload["window_label"] == "借用周期"
        assert payload["range_text"] == "2026-07-27 ~ 2026-08-04"
        assert payload["events"] == 1
        assert payload["cost_usd"] == pytest.approx(14.53)
        assert payload["models"][0]["model"] == "composer-2.5-fast"

        july = build_loan_usage_payload(
            session,
            loan,
            loan_svc,
            mode="calendar_month",
            period="2026-07",
            today=date(2026, 8, 4),
        )
        assert july is not None
        assert july["usage_source"] == "proxy"
        assert july["range_text"] == "2026-07-27 ~ 2026-07-31"
        assert july["events"] == 1
        assert july["cost_usd"] == pytest.approx(14.53)
        assert july["models"][0]["model"] == "composer-2.5-fast"

        august = build_loan_usage_payload(
            session,
            loan,
            loan_svc,
            mode="calendar_month",
            period="2026-08",
            today=date(2026, 8, 4),
        )
        assert august is not None
        assert august["usage_source"] == "quota_approx"
        assert august["events"] == 0
        assert august["cost_usd"] == pytest.approx(0.0)
    finally:
        session.close()


def test_loan_borrowed_quota_pct_from_total_pct():
    loan = SimpleNamespace(baseline_used_cents=1000)
    snapshot = SimpleNamespace(used_cents=2000, total_pct=40.0, limit_cents=5000)
    assert _loan_borrowed_quota_pct(loan, snapshot) == pytest.approx(20.0)


def test_loan_borrowed_quota_pct_fallback_limit_cents():
    loan = SimpleNamespace(baseline_used_cents=500)
    snapshot = SimpleNamespace(used_cents=1500, total_pct=None, limit_cents=10000)
    assert _loan_borrowed_quota_pct(loan, snapshot) == pytest.approx(10.0)


def test_format_includes_loan_section():
    created = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)
    msg = format_usage_self_message(
        mode="billing_cycle",
        period="2026-07",
        accounts=[
            {
                "identifier": "a@x.com",
                "window_label": "记账周期",
                "range_text": "2026-06-15 ~ 2026-07-14",
                "events": 5,
                "tokens": 50,
                "cost_usd": 0.5,
                "models": [{"model": "m1", "events": 5, "tokens": 50, "cost_usd": 0.5}],
            },
            {
                "identifier": "借用 Key",
                "is_loan": True,
                "usage_source": "quota_approx",
                "source_identifier": "lender@x.com",
                "lender_name": "张三",
                "loan_created_at": created,
                "borrowed_quota_pct": 12.5,
                "remaining_headroom_pct": 36.5,
            },
        ],
    )
    assert "a@x.com" in msg
    assert "**借用 Key**" in msg
    assert "借自" not in msg
    assert "张三" not in msg
    assert "7/10 起" in msg
    assert "已消耗" in msg and "12.5%" in msg
    assert "还能用" in msg and "36.5%" in msg
    assert "m2" not in msg
    assert "lender@x.com" not in msg
    assert "我的借用" in msg


def test_format_loan_section_with_proxy():
    created = datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc)
    updated = datetime(2026, 7, 22, 10, 0, 0, tzinfo=timezone.utc)
    msg = format_usage_self_message(
        mode="billing_cycle",
        period="2026-07",
        accounts=[
            {
                "kind": "loan",
                "identifier": "借用 Key",
                "is_loan": True,
                "usage_source": "proxy",
                "lender_name": "Alice",
                "loan_created_at": created,
                "events": 18,
                "tokens": 890000,
                "cost_usd": 2.1,
                "remaining_headroom_pct": 12.3,
                "proxy_data_updated_at": updated,
                "models": [
                    {
                        "model": "claude-opus-4-8",
                        "events": 10,
                        "tokens": 700000,
                        "cost_usd": 1.8,
                    },
                    {
                        "model": "composer-2.5-fast",
                        "events": 8,
                        "tokens": 190000,
                        "cost_usd": 0.3,
                    },
                ],
            }
        ],
    )
    assert "**借用 Key**" in msg
    assert "借自" not in msg
    assert "Alice" not in msg
    assert "Proxy 精确计量" in msg
    assert "≈$2.10" in msg
    assert "还能用" in msg and "12.3%" in msg
    assert "| 模型 | 次数 | Tokens | 估算费用 | 占比 |" in msg
    assert "claude-opus-4-8" in msg
    assert "composer-2.5-fast" in msg
    assert "| 账号 |" not in msg


def test_build_usage_self_payload_empty_schema():
    session = init_db("sqlite:///:memory:")()
    team, _repo = make_team_repo(session)
    config = SimpleNamespace(
        collection=SimpleNamespace(timezone="Asia/Shanghai", period_format="%Y-%m")
    )
    payload = build_usage_self_payload(
        session,
        accounts=[],
        text="我的用量",
        config=config,
        member_id="missing",
        team_id=team.id,
    )
    assert payload["schema_version"] == 1
    assert payload["empty_reason"] == "no_cursor_or_loan"
    assert payload["accounts"] == []
    assert payload["query"]["mode"] in {"billing_cycle", "calendar_month"}
    session.close()


def test_build_usage_self_payload_loan_datetimes_are_json_safe(monkeypatch):
    """Regression: datetime in result must not break capability invoke JSON."""
    import json

    from pulse.tool_center import usage_self as mod

    now = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)
    session = init_db("sqlite:///:memory:")()
    team, _repo = make_team_repo(session)
    config = SimpleNamespace(
        collection=SimpleNamespace(timezone="Asia/Shanghai", period_format="%Y-%m")
    )

    class _FakeLoan:
        pass

    monkeypatch.setattr(
        "pulse.tool_center.key_loans.KeyLoanService.list_active_loans_for_borrower",
        lambda self, member_id: [_FakeLoan()],
    )
    monkeypatch.setattr(
        mod,
        "build_loan_usage_payload",
        lambda *args, **kwargs: {
            "identifier": "借用 Key",
            "is_loan": True,
            "source_identifier": "lender@x.com",
            "lender_name": "张三",
            "loan_created_at": now,
            "approx_borrowed_usd": 1.0,
            "borrowed_quota_pct": 12.5,
            "remaining_headroom_pct": 36.5,
            "quota_captured_at": now,
            "usage_source": "quota_approx",
            "events": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "models": [],
            "proxy_data_updated_at": None,
            "window_label": "记账周期 · 借用段",
            "range_text": "2026-07-01 ~ 2026-07-31",
        },
    )
    payload = build_usage_self_payload(
        session,
        accounts=[],
        text="我的用量",
        config=config,
        member_id="borrower-1",
        team_id=team.id,
        encryption_key="",
    )
    json.dumps(payload)
    loan_row = next(a for a in payload["accounts"] if a.get("kind") == "loan")
    assert loan_row["identifier"] == "借用 Key"
    assert loan_row["usage_source"] == "quota_approx"
    # Tool result must use China local time with explicit offset (not naive UTC).
    assert loan_row["loan_created_at"] == "2026-07-10T17:00:00+08:00"
    assert loan_row["quota_captured_at"] == "2026-07-10T17:00:00+08:00"
    assert loan_row["data_updated_at"] == "2026-07-10T17:00:00+08:00"
    assert loan_row["loan"]["loan_created_at"] == "2026-07-10T17:00:00+08:00"
    session.close()


def test_format_accepts_schema_loan_nested():
    created = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)
    msg = format_usage_self_message(
        mode="billing_cycle",
        period="2026-07",
        accounts=[
            {
                "kind": "loan",
                "identifier": "借用 Key",
                "is_loan": True,
                "usage_source": "quota_approx",
                "loan": {
                    "lender_name": "李四",
                    "source_identifier": "lender@x.com",
                    "loan_created_at": created.isoformat(),
                    "borrowed_quota_pct": 8.0,
                    "remaining_headroom_pct": 40.0,
                    "usage_source": "quota_approx",
                },
            }
        ],
    )
    assert "**借用 Key**" in msg
    assert "借自" not in msg
    assert "李四" not in msg
    assert "7/10 起" in msg
    assert "8.0%" in msg
    assert "40.0%" in msg
