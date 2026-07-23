from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from pulse.extract.csv_parser import parse_usage_events_file, parse_usage_events_xlsx

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"
MINI_EVENT_COUNT = 4


def _write_xlsx_from_csv(csv_path: Path, xlsx_path: Path, *, rename_headers: bool = False) -> None:
    import csv

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if rename_headers:
        fieldnames = [
            "Cloud Agent" if name == "Cloud Agent ID" else
            "Automation Kind" if name == "Kind" else
            name
            for name in fieldnames
        ]
        renamed_rows = []
        for row in rows:
            renamed = {
                new_name: row[old_name]
                for old_name, new_name in zip(reader.fieldnames or [], fieldnames)
            }
            renamed_rows.append(renamed)
        rows = renamed_rows
    df = pd.DataFrame(rows, columns=fieldnames)
    df.to_excel(xlsx_path, index=False, engine="openpyxl")


def test_parse_xlsx_matches_csv(tmp_path: Path):
    xlsx_path = tmp_path / "usage-events.xlsx"
    _write_xlsx_from_csv(SAMPLE, xlsx_path)
    parsed = parse_usage_events_xlsx(xlsx_path)
    csv_parsed = parse_usage_events_file(SAMPLE)
    assert parsed.summary.event_count == csv_parsed.summary.event_count
    assert parsed.summary.total_tokens == csv_parsed.summary.total_tokens
    assert parsed.summary.total_cost_usd == csv_parsed.summary.total_cost_usd


def test_parse_xlsx_with_renamed_headers(tmp_path: Path):
    xlsx_path = tmp_path / "usage-events-new.xlsx"
    _write_xlsx_from_csv(SAMPLE, xlsx_path, rename_headers=True)
    parsed = parse_usage_events_xlsx(xlsx_path)
    assert parsed.summary.event_count == MINI_EVENT_COUNT
    assert parsed.summary.total_cost_usd == Decimal("0.01")


def test_parse_usage_events_file_dispatches_by_suffix(tmp_path: Path):
    xlsx_path = tmp_path / "usage-events.xlsx"
    _write_xlsx_from_csv(SAMPLE, xlsx_path)
    parsed = parse_usage_events_file(xlsx_path)
    assert parsed.summary.event_count == MINI_EVENT_COUNT


def test_unsupported_suffix_raises(tmp_path: Path):
    bad = tmp_path / "usage.txt"
    bad.write_text("not usage", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file type"):
        parse_usage_events_file(bad)
