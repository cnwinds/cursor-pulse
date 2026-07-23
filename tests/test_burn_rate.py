from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.burn_rate import (
    LenderCandidate,
    analyze_burn_rate,
    explain_lender_selection,
    projected_surplus_cents,
    quota_progress,
    recommend_lenders,
    snapshot_freshness,
)

TODAY = date(2026, 7, 10)
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    cycle_start: date,
    cycle_end: date,
    account_id: str = "acc-1",
    limit_cents: int = 7000,
    used_cents: int = 0,
    remaining_cents: int = 7000,
    total_pct: float | None = None,
    auto_pct: float | None = None,
    api_pct: float | None = None,
) -> AccountQuotaSnapshot:
    return AccountQuotaSnapshot(
        account_id=account_id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        limit_cents=limit_cents,
        used_cents=used_cents,
        remaining_cents=remaining_cents,
        total_pct=total_pct,
        auto_pct=auto_pct,
        api_pct=api_pct,
    )


def _candidate(
    snap: AccountQuotaSnapshot,
    *,
    account_id: str,
    identifier: str | None = None,
    renews_on: date | None = None,
    active_loans: int = 0,
) -> LenderCandidate:
    return LenderCandidate(
        snapshot=snap,
        account_id=account_id,
        account_identifier=identifier or f"{account_id}@x.com",
        renews_on=renews_on,
        active_loans=active_loans,
    )


def test_healthy_burn_rate_uses_cursor_total_pct():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        used_cents=15605,
        remaining_cents=0,
        total_pct=24.0,
        auto_pct=27.0,
        api_pct=10.0,
    )
    analysis = analyze_burn_rate(snap, today=date(2026, 7, 13))
    assert analysis.status == "healthy"
    assert quota_progress(snap) == 0.24
    assert analysis.remaining_headroom_pct == 76.0


def test_exhausted_when_total_pct_reaches_100():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        total_pct=100.0,
    )
    analysis = analyze_burn_rate(snap, today=date(2026, 7, 20))
    assert analysis.status == "exhausted"


def test_warning_when_total_pct_high_or_exhausts_before_reset():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        total_pct=70.0,
        auto_pct=50.0,
        api_pct=40.0,
    )
    analysis = analyze_burn_rate(snap, today=date(2026, 7, 5))
    assert analysis.status == "warning"
    assert analysis.exhausts_before_reset is True


def test_recommend_lenders_skips_exhausted_by_total_pct():
    healthy = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a1",
        total_pct=20.0,
        used_cents=1400,
        remaining_cents=5600,
    )
    exhausted = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a2",
        total_pct=100.0,
        used_cents=7000,
        remaining_cents=0,
    )
    ranked = recommend_lenders(
        [
            _candidate(exhausted, account_id="a2"),
            _candidate(healthy, account_id="a1"),
        ],
        today=date(2026, 7, 10),
        now=NOW,
    )
    assert len(ranked) == 1
    assert ranked[0]["account_id"] == "a1"
    assert ranked[0]["remaining_headroom_pct"] == 80.0


def test_recommend_prefers_near_deadline_account_for_digestion():
    far = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="far",
        limit_cents=20000,
        used_cents=2000,
        remaining_cents=18000,
    )
    near = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 13),
        account_id="near",
        limit_cents=7000,
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [
            _candidate(far, account_id="far"),
            _candidate(near, account_id="near"),
        ],
        today=TODAY,
        now=NOW,
    )
    assert [r["account_id"] for r in ranked] == ["near", "far"]
    assert ranked[0]["days_to_deadline"] == 3


def test_recommend_filters_accounts_at_loan_cap():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="full",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [_candidate(snap, account_id="full", active_loans=2)],
        today=TODAY,
        now=NOW,
    )
    assert ranked == []


def test_recommend_penalizes_but_allows_partially_loaded_account():
    loaded = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="loaded",
        used_cents=1000,
        remaining_cents=6000,
    )
    idle = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="idle",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [
            _candidate(loaded, account_id="loaded", active_loans=1),
            _candidate(idle, account_id="idle", active_loans=0),
        ],
        today=TODAY,
        now=NOW,
    )
    assert [r["account_id"] for r in ranked] == ["idle", "loaded"]


def test_recommend_filters_when_under_one_hour_to_deadline():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=TODAY,
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    now = datetime(2026, 7, 10, 23, 30, tzinfo=timezone.utc)  # 距当天作废仅 30 分钟
    ranked = recommend_lenders([_candidate(snap, account_id="a1")], today=TODAY, now=now)
    assert ranked == []


def test_renews_on_overrides_cycle_end_as_deadline():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [_candidate(snap, account_id="a1", renews_on=date(2026, 7, 12))],
        today=TODAY,
        now=NOW,
    )
    assert ranked[0]["days_to_deadline"] == 2
    assert ranked[0]["deadline"] == "2026-07-12"


def test_renews_on_in_past_filters_account():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [_candidate(snap, account_id="a1", renews_on=date(2026, 7, 9))],
        today=TODAY,
        now=NOW,
    )
    assert ranked == []


def test_surplus_excludes_owner_future_consumption():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        limit_cents=7000,
        used_cents=900,
        remaining_cents=6100,
    )
    # 号主日均 100 cents，30 天还要用 3000 → 富余 3100
    assert projected_surplus_cents(snap, 30, today=TODAY) == 3100.0


def test_single_candidate_scores_sum_of_weights():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders([_candidate(snap, account_id="a1")], today=TODAY, now=NOW)
    assert ranked[0]["score"] == 1.0


def test_weight_override_shifts_priority_to_surplus():
    far = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="far",
        limit_cents=20000,
        used_cents=2000,
        remaining_cents=18000,
    )
    near = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 13),
        account_id="near",
        limit_cents=7000,
        used_cents=1000,
        remaining_cents=6000,
    )
    selection = LoanSelectionConfig(
        weight_urgency=0.0,
        weight_surplus=1.0,
        weight_load=0.0,
        weight_freshness=0.0,
    )
    ranked = recommend_lenders(
        [_candidate(near, account_id="near"), _candidate(far, account_id="far")],
        today=TODAY,
        loan_selection=selection,
        now=NOW,
    )
    assert ranked[0]["account_id"] == "far"


def test_tie_break_by_account_id():
    fixed = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    snap_b = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="b",
        used_cents=1000,
        remaining_cents=6000,
    )
    snap_a = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a",
        used_cents=1000,
        remaining_cents=6000,
    )
    snap_b.captured_at = fixed
    snap_a.captured_at = fixed
    ranked = recommend_lenders(
        [_candidate(snap_b, account_id="b"), _candidate(snap_a, account_id="a")],
        today=TODAY,
        now=fixed,
    )
    assert [r["account_id"] for r in ranked] == ["a", "b"]


def test_surplus_falls_back_to_pct_path_when_remaining_cents_zero():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        limit_cents=7000,
        used_cents=0,
        remaining_cents=0,
        total_pct=20.0,
    )
    # elapsed=9 → daily_pct=20/9；10 天再耗 22.22% → surplus_pct=57.78% × 7000
    assert projected_surplus_cents(snap, 10, today=TODAY) == 4044.44


def test_surplus_zero_when_no_cents_and_no_pct():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        limit_cents=7000,
        used_cents=0,
        remaining_cents=0,
        total_pct=None,
    )
    assert projected_surplus_cents(snap, 10, today=TODAY) == 0.0


def test_surplus_zero_when_limit_unknown_on_pct_path():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        limit_cents=0,
        used_cents=0,
        remaining_cents=0,
        total_pct=20.0,
    )
    assert projected_surplus_cents(snap, 10, today=TODAY) == 0.0


def test_same_day_deadline_over_one_hour_passes():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=TODAY,  # 当天作废，但距作废 > 1 小时即可借
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    ranked = recommend_lenders([_candidate(snap, account_id="a1")], today=TODAY, now=now)
    assert len(ranked) == 1
    assert ranked[0]["days_to_deadline"] == 0
    assert ranked[0]["hours_to_deadline"] == 12.0


def test_shorter_deadline_gets_more_urgency_weight():
    same_day = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=TODAY,  # 当天作废
        account_id="same-day",
        used_cents=0,
        remaining_cents=6000,
    )
    five_day = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 15),
        account_id="five-day",
        used_cents=0,
        remaining_cents=6000,
    )
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    ranked = recommend_lenders(
        [
            _candidate(five_day, account_id="five-day"),
            _candidate(same_day, account_id="same-day"),
        ],
        today=TODAY,
        now=now,
    )
    assert ranked[0]["account_id"] == "same-day"
    # 相同富余：12h vs 132h → urgency 约 11 倍
    assert ranked[0]["urgency_cents_per_day"] > ranked[1]["urgency_cents_per_day"] * 10


def test_zero_min_coverage_with_deadline_today_uses_hour_floor():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=TODAY,
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    selection = LoanSelectionConfig(min_coverage_hours=0)
    now = datetime(2026, 7, 10, 23, 30, tzinfo=timezone.utc)  # 仅剩 0.5h → 触发 1h 下限
    ranked = recommend_lenders(
        [_candidate(snap, account_id="a1")], today=TODAY, now=now, loan_selection=selection
    )
    assert len(ranked) == 1
    # urgency 除数下限为 1 小时（1/24 天）：urgency = surplus × 24
    assert ranked[0]["urgency_cents_per_day"] == round(ranked[0]["surplus_cents"] * 24, 2)


def test_load_factor_scales_with_configured_cap():
    selection = LoanSelectionConfig(
        max_active_loans_per_account=3,
        weight_urgency=0.0,
        weight_surplus=0.0,
        weight_load=1.0,
        weight_freshness=0.0,
    )
    snaps = [
        _snapshot(
            cycle_start=date(2026, 7, 1),
            cycle_end=date(2026, 8, 1),
            account_id=acc_id,
            used_cents=1000,
            remaining_cents=6000,
        )
        for acc_id in ("l0", "l1", "l2")
    ]
    ranked = recommend_lenders(
        [
            _candidate(snaps[2], account_id="l2", active_loans=2),
            _candidate(snaps[0], account_id="l0", active_loans=0),
            _candidate(snaps[1], account_id="l1", active_loans=1),
        ],
        today=TODAY,
        loan_selection=selection,
        now=NOW,
    )
    assert [r["account_id"] for r in ranked] == ["l0", "l1", "l2"]
    assert [r["score"] for r in ranked] == [1.0, round(2 / 3, 4), round(1 / 3, 4)]


def test_explain_lender_selection_matches_recommend_and_reasons():
    healthy = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="ok",
        used_cents=1000,
        remaining_cents=6000,
        total_pct=20.0,
    )
    exhausted = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="ex",
        used_cents=7000,
        remaining_cents=0,
        total_pct=100.0,
    )
    capped = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="cap",
        used_cents=1000,
        remaining_cents=6000,
        total_pct=20.0,
    )
    short = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=TODAY,
        account_id="short",
        used_cents=1000,
        remaining_cents=6000,
        total_pct=20.0,
    )
    # 距当天作废仅 30 分钟 → coverage_too_short
    near_eod = datetime(2026, 7, 10, 23, 30, tzinfo=timezone.utc)
    cands = [
        _candidate(healthy, account_id="ok"),
        _candidate(exhausted, account_id="ex"),
        _candidate(capped, account_id="cap", active_loans=2),
        _candidate(short, account_id="short"),
    ]
    board = explain_lender_selection(cands, today=TODAY, now=near_eod)
    ranked = recommend_lenders(cands, today=TODAY, now=near_eod)
    assert board["ranked"] == ranked
    reasons = {e["account_id"]: e["reason"] for e in board["excluded"]}
    assert reasons == {
        "ex": "exhausted",
        "cap": "loan_cap",
        "short": "coverage_too_short",
    }


def test_explain_pool_mode_ignores_loan_cap():
    """代理池路径：在借达上限不排除，且不因 load 压分。"""
    idle = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="idle",
        used_cents=1000,
        remaining_cents=6000,
        total_pct=20.0,
    )
    busy = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="busy",
        used_cents=1000,
        remaining_cents=6000,
        total_pct=20.0,
    )
    cands = [
        _candidate(idle, account_id="idle", active_loans=0),
        _candidate(busy, account_id="busy", active_loans=5),
    ]
    board = explain_lender_selection(
        cands, today=TODAY, now=NOW, enforce_loan_cap=False
    )
    assert {e["reason"] for e in board["excluded"]} == set()
    ranked_ids = [r["account_id"] for r in board["ranked"]]
    assert set(ranked_ids) == {"idle", "busy"}
    # 忽略 load 后同质候选分应一致（仅 account_id 打平）
    assert board["ranked"][0]["score"] == board["ranked"][1]["score"]
    by_id = {r["account_id"]: r for r in board["ranked"]}
    assert by_id["busy"]["active_loans"] == 5


def test_snapshot_freshness_decays_and_floors():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1), cycle_end=date(2026, 8, 1), account_id="a1"
    )
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    snap.captured_at = now - timedelta(hours=12)
    assert snapshot_freshness(snap, 24.0, now) == 0.5
    snap.captured_at = now - timedelta(hours=36)
    assert snapshot_freshness(snap, 24.0, now) == 0.0
    snap.captured_at = now + timedelta(hours=1)  # 未来（时钟偏差）→ 钳到 1
    assert snapshot_freshness(snap, 24.0, now) == 1.0
    snap.captured_at = (now - timedelta(hours=12)).replace(tzinfo=None)  # naive 按 UTC
    assert snapshot_freshness(snap, 24.0, now) == 0.5
    assert snapshot_freshness(snap, 0.0, now) == 1.0  # 尺度 ≤0 恒 1
