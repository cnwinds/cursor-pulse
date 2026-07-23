from datetime import date, datetime

from pulse.util.json_codec import dumps_json


def test_dumps_json_handles_dates():
    payload = {
        "billing_cycle_start": date(2026, 6, 24),
        "billing_cycle_end": date(2026, 7, 24),
        "computed_at": datetime(2026, 6, 1, 12, 0, 0),
        "nested": [{"when": date(2026, 1, 1)}],
    }
    text = dumps_json(payload)
    assert '"billing_cycle_start": "2026-06-24"' in text
    assert '"billing_cycle_end": "2026-07-24"' in text
    assert '"computed_at": "2026-06-01T12:00:00"' in text
