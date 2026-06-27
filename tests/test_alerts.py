from pulse.alerts.engine import detect_anomalies


def test_detect_team_events_spike():
    current = {
        "period": "2026-06",
        "total_events": 200,
        "member_names": {},
        "events_by_member": [],
        "unsubmitted_members": [],
    }
    previous = {"total_events": 100, "events_by_member": [], "cost_by_member": []}
    alerts = detect_anomalies(current, previous, team_events_spike_pct=50)
    assert any(a.alert_type == "team_events_spike" for a in alerts)


def test_detect_member_cost_spike():
    current = {
        "period": "2026-06",
        "member_names": {"m1": "Alice"},
        "events_by_member": [],
        "cost_by_member": [{"member_id": "m1", "value": 25.0, "rank": 1}],
        "unsubmitted_members": [],
    }
    previous = {
        "events_by_member": [],
        "cost_by_member": [{"member_id": "m1", "value": 5.0, "rank": 1}],
    }
    alerts = detect_anomalies(current, previous, member_cost_spike_usd=10)
    assert any(a.alert_type == "member_cost_spike" for a in alerts)
