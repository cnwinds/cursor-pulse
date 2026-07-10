from pathlib import Path

from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.extract.period_split import periods_in_records, split_parsed_by_period

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"


def test_split_sample_by_natural_month():
    parsed = parse_usage_events_csv(SAMPLE)
    splits = split_parsed_by_period(parsed)

    assert list(splits.keys()) == ["2026-03", "2026-04", "2026-05", "2026-06"]
    assert splits["2026-03"].summary.event_count == 81
    assert splits["2026-04"].summary.event_count == 260
    assert splits["2026-05"].summary.event_count == 107
    assert splits["2026-06"].summary.event_count == 50
    assert sum(s.summary.event_count for s in splits.values()) == parsed.summary.event_count


def test_periods_in_records_sorted():
    parsed = parse_usage_events_csv(SAMPLE)
    assert periods_in_records(parsed.records) == ["2026-03", "2026-04", "2026-05", "2026-06"]
