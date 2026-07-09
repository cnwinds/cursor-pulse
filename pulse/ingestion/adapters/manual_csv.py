from __future__ import annotations

from pulse.domain import UsageEventRecord
from pulse.extract.csv_parser import parse_usage_events_file
from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import UsageEventDTO


def _record_to_dto(rec: UsageEventRecord) -> UsageEventDTO:
    return UsageEventDTO(
        event_at=rec.event_at,
        event_date=rec.event_date,
        model=rec.model,
        kind=rec.kind,
        tokens_input_cache_write=rec.tokens_input_cache_write,
        tokens_input_no_cache=rec.tokens_input_no_cache,
        tokens_cache_read=rec.tokens_cache_read,
        tokens_output=rec.tokens_output,
        tokens_total=rec.tokens_total,
        cost_usd=float(rec.cost_usd),
        cost_raw=rec.cost_raw.value,
        external_id=rec.source_row_hash,
        source_row_hash=rec.source_row_hash,
    )


class ManualCsvAdapter:
    vendor_slug = None
    source_type = "manual_csv"

    def can_handle(self, context: IngestionContext) -> bool:
        return context.source_type == "manual_csv" and context.vendor_slug != "cursor"

    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]:
        if context.events:
            return list(context.events)
        if not context.raw_file_path:
            return []
        parsed = parse_usage_events_file(context.raw_file_path)
        return [_record_to_dto(rec) for rec in parsed.records]

    def extract_metadata(self, context: IngestionContext) -> dict:
        if context.metadata:
            return dict(context.metadata)
        if not context.raw_file_path:
            return {}
        parsed = parse_usage_events_file(context.raw_file_path)
        summary = parsed.summary
        return {
            "period_hint": summary.period_hint,
            "date_min": summary.date_min.isoformat(),
            "date_max": summary.date_max.isoformat(),
            "event_count": summary.event_count,
            "total_cost_usd": str(summary.total_cost_usd),
        }

    def requires_review(self) -> bool:
        return False
