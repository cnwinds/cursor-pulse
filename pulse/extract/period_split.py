from __future__ import annotations

from collections import defaultdict

from pulse.domain import ParsedCsv, UsageEventRecord
from pulse.extract.csv_parser import build_parse_summary


def periods_in_records(records: list[UsageEventRecord]) -> list[str]:
    return sorted({r.event_date.strftime("%Y-%m") for r in records})


def split_parsed_by_period(parsed: ParsedCsv) -> dict[str, ParsedCsv]:
    buckets: dict[str, list[UsageEventRecord]] = defaultdict(list)
    for rec in parsed.records:
        buckets[rec.event_date.strftime("%Y-%m")].append(rec)

    return {
        period: ParsedCsv(records=records, summary=build_parse_summary(records))
        for period, records in sorted(buckets.items())
    }
