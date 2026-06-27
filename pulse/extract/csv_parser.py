from __future__ import annotations

import csv
import hashlib
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

from pulse.domain import CostRaw, ParseSummary, ParsedCsv, UsageEventRecord

EXPECTED_HEADERS = [
    "Date",
    "Cloud Agent ID",
    "Automation ID",
    "Kind",
    "Model",
    "Max Mode",
    "Input (w/ Cache Write)",
    "Input (w/o Cache Write)",
    "Cache Read",
    "Output Tokens",
    "Total Tokens",
    "Cost",
]


def _parse_int(value: str) -> int:
    value = (value or "").strip()
    if not value:
        return 0
    return int(value)


def _parse_cost(value: str) -> tuple[CostRaw, Decimal]:
    raw = (value or "").strip()
    if raw == "Included":
        return CostRaw.INCLUDED, Decimal("0")
    if raw == "Free":
        return CostRaw.FREE, Decimal("0")
    if raw in ("-", ""):
        return CostRaw.NONE, Decimal("0")
    try:
        return CostRaw.USAGE_BASED, Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Unrecognized cost value: {raw!r}") from exc


def _parse_datetime(value: str) -> datetime:
    text = value.strip().strip('"')
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_hash(row: dict[str, str]) -> str:
    payload = "|".join(row.get(h, "") for h in EXPECTED_HEADERS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_from_mapped_row(mapped: dict[str, str]) -> UsageEventRecord:
    event_at = _parse_datetime(mapped["Date"])
    cost_raw, cost_usd = _parse_cost(mapped["Cost"])
    max_mode_raw = (mapped.get("Max Mode") or "No").strip().lower()
    return UsageEventRecord(
        event_at=event_at,
        event_date=event_at.date(),
        kind=mapped.get("Kind") or "Unknown",
        model=mapped.get("Model") or "unknown",
        max_mode=max_mode_raw in ("yes", "true", "1"),
        tokens_input_cache_write=_parse_int(mapped.get("Input (w/ Cache Write)", "0")),
        tokens_input_no_cache=_parse_int(mapped.get("Input (w/o Cache Write)", "0")),
        tokens_cache_read=_parse_int(mapped.get("Cache Read", "0")),
        tokens_output=_parse_int(mapped.get("Output Tokens", "0")),
        tokens_total=_parse_int(mapped.get("Total Tokens", "0")),
        cost_raw=cost_raw,
        cost_usd=cost_usd,
        cloud_agent_id=(mapped.get("Cloud Agent ID") or "") or None,
        automation_id=(mapped.get("Automation ID") or "") or None,
        source_row_hash=_row_hash(mapped),
    )


def build_parse_summary(records: list[UsageEventRecord]) -> ParseSummary:
    dates = [r.event_date for r in records]
    date_min, date_max = min(dates), max(dates)
    total_tokens = sum(r.tokens_total for r in records)
    total_cost = sum(r.cost_usd for r in records)
    model_counts = Counter(r.model for r in records)
    top_models = model_counts.most_common(3)
    all_included = total_cost == Decimal("0")
    period_hint = date_max.strftime("%Y-%m")
    return ParseSummary(
        period_hint=period_hint,
        date_min=date_min,
        date_max=date_max,
        event_count=len(records),
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        top_models=top_models,
        all_included_or_free=all_included,
    )


def _normalize_headers(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        raise ValueError("CSV has no header row")
    mapping: dict[str, str] = {}
    for name in fieldnames:
        key = name.strip()
        mapping[key.lower()] = key
    normalized: dict[str, str] = {}
    for expected in EXPECTED_HEADERS:
        found = mapping.get(expected.lower())
        if not found:
            raise ValueError(f"Missing required column: {expected}")
        normalized[expected] = found
    return normalized


def parse_usage_events_csv(content: str | bytes | Path) -> ParsedCsv:
    if isinstance(content, Path):
        text = content.read_text(encoding="utf-8-sig")
    elif isinstance(content, bytes):
        text = content.decode("utf-8-sig")
    else:
        text = content

    reader = csv.DictReader(StringIO(text))
    col_map = _normalize_headers(reader.fieldnames)

    records: list[UsageEventRecord] = []
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        mapped = {expected: (row.get(col_map[expected]) or "").strip() for expected in EXPECTED_HEADERS}
        records.append(record_from_mapped_row(mapped))

    if not records:
        raise ValueError("CSV contains no data rows")

    summary = build_parse_summary(records)
    return ParsedCsv(records=records, summary=summary)
