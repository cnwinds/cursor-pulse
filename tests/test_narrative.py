from __future__ import annotations

from pulse.report.narrative import generate_insights_with_fallback
from pulse.report.insights import generate_insights


class FakeLLMClient:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.response


def test_fallback_to_rules_when_no_client():
    metrics = {
        "member_names": {"m1": "Alice"},
        "events_by_member": [{"member_id": "m1", "value": 10, "rank": 1}],
        "mom_events_change_pct": None,
        "events_by_model": {"auto": 10},
        "unsubmitted_members": [],
        "total_cost_usd": 0,
    }
    text, source = generate_insights_with_fallback(metrics, None)
    assert source == "rules"
    assert text == generate_insights(metrics)


def test_llm_insights_when_valid():
    metrics = {
        "total_events": 100,
        "mom_events_change_pct": 25.0,
        "member_names": {"m1": "Alice"},
        "events_by_member": [{"member_id": "m1", "value": 100, "rank": 1}],
        "events_by_model": {"auto": 100},
        "unsubmitted_members": [],
        "total_cost_usd": 0,
    }
    client = FakeLLMClient("### 简要洞察\n\n- 整体请求量环比上升 25.0%。")
    text, source = generate_insights_with_fallback(metrics, client)
    assert source == "llm"
    assert "25.0%" in text
    assert client.calls == 1


def test_llm_fallback_on_hallucinated_number():
    metrics = {"total_events": 100, "mom_events_change_pct": 25.0}
    client = FakeLLMClient("【洞察】\n· 用量暴增 99.9%。")
    text, source = generate_insights_with_fallback(metrics, client)
    assert source == "rules"
    assert "### 简要洞察" in text
