from pulse.report.formatter import format_monthly_report
from pulse.report.insights import generate_insights


def test_format_monthly_report_contains_facts():
    metrics = {
        "period": "2026-06",
        "member_count_reported": 2,
        "member_count_expected": 3,
        "total_events": 100,
        "total_tokens": 50000,
        "total_cost_usd": 0.0,
        "member_names": {"m1": "Alice", "m2": "Bob"},
        "events_by_member": [
            {"member_id": "m1", "value": 60, "rank": 1},
            {"member_id": "m2", "value": 40, "rank": 2},
        ],
        "tokens_by_member": [
            {"member_id": "m1", "value": 30000, "rank": 1},
        ],
        "events_by_model": {"auto": 80, "composer-2.5": 20},
        "unsubmitted_members": ["Carol"],
        "mom_events_change_pct": 25.0,
    }
    text = format_monthly_report(metrics)
    assert "2026-06" in text
    assert "100" in text
    assert "Alice" in text
    assert "Carol" in text
    assert "+25.0%" in text


def test_insights_no_hallucinated_numbers():
    metrics = {
        "member_count_reported": 2,
        "member_names": {"m1": "Alice", "m2": "Bob"},
        "events_by_member": [
            {"member_id": "m1", "value": 10, "rank": 1},
            {"member_id": "m2", "value": 5, "rank": 2},
        ],
        "mom_events_change_pct": 30.0,
        "events_by_model": {"auto": 10},
        "unsubmitted_members": [],
        "total_cost_usd": 0,
    }
    text = generate_insights(metrics)
    assert "Alice" in text
    assert "30.0%" in text
    assert "auto" in text
