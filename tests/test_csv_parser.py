from decimal import Decimal
from pathlib import Path

import pytest

from pulse.domain import CostRaw
from pulse.extract.csv_parser import parse_usage_events_csv

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"
MINI_EVENT_COUNT = 4


def test_parse_mini_row_count():
    parsed = parse_usage_events_csv(SAMPLE)
    assert parsed.summary.event_count == MINI_EVENT_COUNT


def test_parse_mini_cost_breakdown():
    parsed = parse_usage_events_csv(SAMPLE)
    included = sum(1 for r in parsed.records if r.cost_raw == CostRaw.INCLUDED)
    usage_based = sum(1 for r in parsed.records if r.cost_raw == CostRaw.USAGE_BASED)
    none_cost = sum(1 for r in parsed.records if r.cost_raw == CostRaw.NONE)
    free = sum(1 for r in parsed.records if r.cost_raw == CostRaw.FREE)
    assert included == 1
    assert usage_based == 1
    assert none_cost == 1
    assert free == 1


def test_parse_mini_total_cost():
    parsed = parse_usage_events_csv(SAMPLE)
    assert parsed.summary.total_cost_usd == Decimal("0.01")
    assert parsed.summary.all_included_or_free is False


def test_parse_mini_tokens_positive():
    parsed = parse_usage_events_csv(SAMPLE)
    assert parsed.summary.total_tokens > 0


def test_source_row_hash_unique():
    parsed = parse_usage_events_csv(SAMPLE)
    hashes = [r.source_row_hash for r in parsed.records]
    assert len(hashes) == len(set(hashes))


def test_missing_column_raises():
    with pytest.raises(ValueError, match="Missing required column"):
        parse_usage_events_csv("Date,Model\n2026-01-01,auto\n")
