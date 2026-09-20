from __future__ import annotations

from datetime import date, timedelta, timezone

from pulse.config import LoanSelectionConfig
from pulse.tool_center.burn_rate import (
    apply_switch_cooldown,
    explain_lender_selection,
    projected_surplus_cents_for_pool,
    recommend_lenders,
)
from pulse.tool_center.jev_rank import blend_jev_answers, score_assignment_candidates
from pulse.tool_center.snapshot_headroom import (
    quota_pool_for_model,
    resolve_quota_pool,
)
from tests.test_burn_rate import NOW, TODAY, _candidate, _snapshot


def test_quota_pool_for_model_matches_go_heuristics():
    assert quota_pool_for_model("default") == "auto"
    assert quota_pool_for_model("composer-2") == "auto"
    assert quota_pool_for_model("grok-4") == "auto"
    assert quota_pool_for_model("claude-4-sonnet") == "api"
    assert quota_pool_for_model("GLM-5.2") == "unknown"
    assert resolve_quota_pool(quota_pool="default") == "auto"
    assert resolve_quota_pool(model="composer-2") == "auto"
    assert resolve_quota_pool(quota_pool="api", model="default") == "api"


def test_api_pool_prefers_account_with_api_surplus():
    api_left = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="api-left",
        total_pct=40.0,
        auto_pct=90.0,
        api_pct=10.0,
    )
    auto_left = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="auto-left",
        total_pct=40.0,
        auto_pct=10.0,
        api_pct=90.0,
    )
    cands = [
        _candidate(api_left, account_id="api-left"),
        _candidate(auto_left, account_id="auto-left"),
    ]
    api_ranked = recommend_lenders(cands, today=TODAY, now=NOW, quota_pool="api")
    auto_ranked = recommend_lenders(cands, today=TODAY, now=NOW, quota_pool="auto")
    assert api_ranked[0]["account_id"] == "api-left"
    assert auto_ranked[0]["account_id"] == "auto-left"
    assert api_ranked[0]["quota_pool"] == "api"


def test_api_pool_excludes_full_api_bucket():
    full_api = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="full-api",
        total_pct=50.0,
        auto_pct=10.0,
        api_pct=100.0,
    )
    ok = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="ok",
        total_pct=20.0,
        auto_pct=20.0,
        api_pct=10.0,
    )
    board = explain_lender_selection(
        [_candidate(full_api, account_id="full-api"), _candidate(ok, account_id="ok")],
        today=TODAY,
        now=NOW,
        quota_pool="api",
    )
    assert [r["account_id"] for r in board["ranked"]] == ["ok"]
    assert {e["account_id"]: e["reason"] for e in board["excluded"]} == {
        "full-api": "exhausted"
    }


def test_pool_surplus_uses_requested_bucket_not_total():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="split",
        limit_cents=10000,
        total_pct=50.0,
        auto_pct=80.0,
        api_pct=10.0,
    )
    auto_surplus = projected_surplus_cents_for_pool(
        snap, 10.0, TODAY, quota_pool="auto"
    )
    api_surplus = projected_surplus_cents_for_pool(snap, 10.0, TODAY, quota_pool="api")
    assert api_surplus > auto_surplus


def test_manual_adjust_applies_on_loan_path():
    soon = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 12),
        account_id="soon",
        used_cents=100,
        remaining_cents=2000,
        total_pct=5.0,
    )
    far = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 4),
        account_id="far",
        limit_cents=20000,
        used_cents=2000,
        remaining_cents=18000,
        total_pct=10.0,
    )
    natural = recommend_lenders(
        [_candidate(far, account_id="far"), _candidate(soon, account_id="soon")],
        today=TODAY,
        now=NOW,
    )
    boosted = recommend_lenders(
        [
            _candidate(far, account_id="far", score_adjust=1.0),
            _candidate(soon, account_id="soon"),
        ],
        today=TODAY,
        now=NOW,
    )
    assert natural[0]["account_id"] == "soon"
    assert boosted[0]["account_id"] == "far"


def test_switch_cooldown_keeps_sticky_account():
    a = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a",
        total_pct=20.0,
    )
    b = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 12),
        account_id="b",
        total_pct=20.0,
    )
    ranked = recommend_lenders(
        [_candidate(a, account_id="a"), _candidate(b, account_id="b")],
        today=TODAY,
        now=NOW,
        loan_selection=LoanSelectionConfig(min_switch_minutes=30),
        sticky_account_id="a",
        sticky_since=NOW - timedelta(minutes=10),
    )
    assert ranked[0]["account_id"] == "a"
    assert ranked[0]["sticky_kept"] is True
    aged = recommend_lenders(
        [_candidate(a, account_id="a"), _candidate(b, account_id="b")],
        today=TODAY,
        now=NOW,
        loan_selection=LoanSelectionConfig(min_switch_minutes=30),
        sticky_account_id="a",
        sticky_since=NOW - timedelta(minutes=45),
    )
    assert aged[0]["account_id"] == "b"
    assert aged[0]["sticky_kept"] is False


def test_switch_cooldown_does_not_keep_excluded_account():
    full = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="full",
        total_pct=100.0,
        auto_pct=100.0,
        api_pct=100.0,
        remaining_cents=0,
        used_cents=7000,
    )
    ok = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="ok",
        total_pct=20.0,
    )
    ranked = recommend_lenders(
        [_candidate(full, account_id="full"), _candidate(ok, account_id="ok")],
        today=TODAY,
        now=NOW,
        sticky_account_id="full",
        sticky_since=NOW - timedelta(minutes=5),
    )
    assert ranked[0]["account_id"] == "ok"


def test_jev_blend_reorders_when_weight_positive():
    low = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 12),
        account_id="urgent",
        total_pct=20.0,
    )
    high = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 4),
        account_id="roomy",
        total_pct=20.0,
        limit_cents=20000,
    )
    cfg = LoanSelectionConfig(weight_jev=1.0)
    ranked = recommend_lenders(
        [_candidate(low, account_id="urgent"), _candidate(high, account_id="roomy")],
        today=TODAY,
        now=NOW,
        loan_selection=cfg,
        jev_scores={"urgent": 0.1, "roomy": 0.9},
    )
    assert ranked[0]["account_id"] == "roomy"
    assert ranked[0]["jev_score"] == 0.9


def test_blend_jev_answers_weights_waste_safe_fit():
    answers = {
        "waste:a1": {"type": "noul", "noul": 1.0},
        "safe:a1": {"type": "noul", "noul": 1.0},
        "fit:a1": {
            "type": "score",
            "score": 3.0,
            "confidence": 0.9,
            "legend": {"0": "p", "1": "u", "2": "g", "3": "e"},
            "probabilities": {"3": 1.0},
        },
    }
    scored = blend_jev_answers(answers, "a1")
    assert scored.blend == 1.0
    assert scored.confidence == 0.9


class _FakeJev:
    def system_one(self, *, state, questions, model=None):
        answers = {}
        for key in questions:
            if key.startswith("waste:") or key.startswith("safe:"):
                answers[key] = {"type": "noul", "noul": 0.8}
            else:
                answers[key] = {"type": "score", "score": 2.0, "confidence": 0.7}
        return {"answers": answers, "model": "jev-test"}


def test_score_assignment_candidates_fail_open_on_error():
    class Boom:
        def system_one(self, **kwargs):
            raise RuntimeError("down")

    out = score_assignment_candidates(
        Boom(), [{"account_id": "a1", "surplus_cents": 100}], quota_pool="api"
    )
    assert out == {}


def test_score_assignment_candidates_returns_blends():
    out = score_assignment_candidates(
        _FakeJev(),
        [{"account_id": "a1", "surplus_cents": 100, "hours_to_deadline": 48}],
        quota_pool="api",
        min_confidence=0.35,
    )
    assert "a1" in out
    assert 0 < out["a1"].blend <= 1


def test_apply_switch_cooldown_helper_noop_without_sticky():
    rows = [{"account_id": "a"}, {"account_id": "b"}]
    assert apply_switch_cooldown(
        rows,
        sticky_account_id=None,
        sticky_since=None,
        now=NOW,
        min_switch_minutes=30,
    ) == rows


def test_jev_blend_skips_low_confidence_fit():
    answers = {
        "waste:a1": {"type": "noul", "noul": 1.0},
        "safe:a1": {"type": "noul", "noul": 1.0},
        "fit:a1": {
            "type": "score",
            "score": 3.0,
            "confidence": 0.1,
            "legend": {"0": "p", "1": "u", "2": "g", "3": "e"},
            "probabilities": {"3": 1.0},
        },
    }

    class LowConf:
        def system_one(self, **kwargs):
            return {"answers": answers, "model": "jev-test"}

    out = score_assignment_candidates(
        LowConf(),
        [{"account_id": "a1", "surplus_cents": 100}],
        quota_pool="api",
        min_confidence=0.35,
    )
    assert out == {}
