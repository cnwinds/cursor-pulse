"""Contract for quota-board UsageSummary picking (mirrors web-admin preferSummaryForBoardCycle).

loadSummaries fetches the union of calendar months for *all* board cards. Without
rejecting rows whose billing_cycle_start ≠ the card's snapshot cycle, a previous
month (e.g. July) sticks on an account whose current cycle is August — so
「本周期用量明细」 disagrees with the 「明细」 dialog date range.
"""

from __future__ import annotations


def prefer_summary_for_board_cycle(
    current: dict | None,
    nxt: dict,
    cycle_start: str | None,
) -> dict | None:
    """Keep in sync with web-admin/src/utils/usage.ts preferSummaryForBoardCycle."""
    if cycle_start and nxt.get("billing_cycle_start") != cycle_start:
        return current
    if not current:
        return nxt
    cur_api = len(
        ((current.get("cursor_pools") or {}).get("api") or {}).get("breakdown_by_model")
        or {}
    )
    next_api = len(
        ((nxt.get("cursor_pools") or {}).get("api") or {}).get("breakdown_by_model") or {}
    )
    if next_api != cur_api:
        return nxt if next_api > cur_api else current
    return nxt if (nxt.get("period") or "") > (current.get("period") or "") else current


def test_reject_previous_month_when_cycle_does_not_match():
    """李景双-style: July row arrives via other accounts' period union; must not stick."""
    cycle_start = "2026-08-02"
    july = {
        "period": "2026-07",
        "billing_cycle_start": "2026-07-01",
        "cursor_pools": {
            "api": {
                "spend_usd": 23.5376,
                "breakdown_by_model": {"claude-opus-4-8-thinking-high": 23.5376},
            }
        },
    }
    merged = prefer_summary_for_board_cycle(None, july, cycle_start)
    assert merged is None


def test_keep_matching_cycle_and_prefer_newer_period():
    cycle_start = "2026-07-24"
    july = {
        "period": "2026-07",
        "billing_cycle_start": cycle_start,
        "cursor_pools": {"api": {"breakdown_by_model": {"a": 1.0}}},
    }
    august = {
        "period": "2026-08",
        "billing_cycle_start": cycle_start,
        "cursor_pools": {"api": {"breakdown_by_model": {"a": 1.0}}},
    }
    merged = prefer_summary_for_board_cycle(None, july, cycle_start)
    merged = prefer_summary_for_board_cycle(merged, august, cycle_start)
    assert merged is not None
    assert merged["period"] == "2026-08"


def test_matching_cycle_beats_denser_mismatch():
    cycle_start = "2026-08-02"
    stale_dense = {
        "period": "2026-07",
        "billing_cycle_start": "2026-07-01",
        "cursor_pools": {
            "api": {"breakdown_by_model": {"a": 1.0, "b": 2.0, "c": 3.0}},
        },
    }
    current_sparse = {
        "period": "2026-08",
        "billing_cycle_start": cycle_start,
        "cursor_pools": {"api": {"breakdown_by_model": {"a": 1.0}}},
    }
    merged = prefer_summary_for_board_cycle(None, stale_dense, cycle_start)
    assert merged is None
    merged = prefer_summary_for_board_cycle(merged, current_sparse, cycle_start)
    assert merged is not None
    assert merged["period"] == "2026-08"
