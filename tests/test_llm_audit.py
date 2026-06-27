from __future__ import annotations

from pulse.llm.audit import find_unauthorized_numbers, numbers_in_text


def test_numbers_in_text_parses_commas_and_percent():
    assert numbers_in_text("环比上升 25.0%，共 1,234 次") == {"25.0", "1234"}


def test_find_unauthorized_numbers_clean():
    metrics = {
        "total_events": 100,
        "mom_events_change_pct": 25.0,
        "member_names": {"m1": "Alice"},
        "events_by_member": [{"member_id": "m1", "value": 100, "rank": 1}],
    }
    narrative = "【洞察】\n· 整体请求量环比上升 25.0%。\n· 请求数最高的是 Alice（100 次）。"
    assert find_unauthorized_numbers(narrative, metrics) == []


def test_find_unauthorized_numbers_detects_hallucination():
    metrics = {"total_events": 100, "mom_events_change_pct": 25.0}
    narrative = "【洞察】\n· 用量暴增 99.9%，需关注。"
    assert "99.9" in find_unauthorized_numbers(narrative, metrics)
