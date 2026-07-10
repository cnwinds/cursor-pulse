from pathlib import Path

from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.extract.summary import format_auto_split_notice, format_split_period_confirmation
from pulse.extract.period_split import split_parsed_by_period

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"


def test_format_auto_split_notice_multi_month():
    notice = format_auto_split_notice(["2026-03", "2026-04", "2026-05", "2026-06"], "2026-07")
    assert "自动拆分" in notice
    assert "2026-06" in notice


def test_format_auto_split_notice_single_historical_month():
    notice = format_auto_split_notice(["2026-06"], "2026-07")
    assert "2026-06" in notice
    assert "2026-07" in notice


def test_format_split_period_confirmation_multi_month():
    parsed = parse_usage_events_csv(SAMPLE)
    splits = split_parsed_by_period(parsed)
    period_summaries = [(p, splits[p].summary) for p in splits]
    text = format_split_period_confirmation("熊波", period_summaries, parsed.summary)
    assert "按账期分别录入" in text
    assert "2026-03" in text
    assert "覆盖更新" in text
