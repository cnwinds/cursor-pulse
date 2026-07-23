from __future__ import annotations

from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import UsageEventDTO
from pulse.tool_center.manual import parse_manual_usage_text


class ManualTextAdapter:
    vendor_slug = None
    source_type = "manual_text"

    def can_handle(self, context: IngestionContext) -> bool:
        return context.source_type == "manual_text" and context.vendor_slug != "cursor"

    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]:
        return list(context.events)

    def extract_metadata(self, context: IngestionContext) -> dict:
        if context.metadata:
            return dict(context.metadata)
        if not context.raw_text:
            return {}
        command = parse_manual_usage_text(context.raw_text)
        return {
            "vendor_slug": command.vendor_slug,
            "metric_value": command.metric_value,
            "metric_unit": command.metric_unit,
            "raw_text": command.raw_text,
        }

    def requires_review(self) -> bool:
        return False
