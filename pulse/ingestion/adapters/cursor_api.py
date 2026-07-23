from __future__ import annotations

from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import UsageEventDTO


class CursorApiAdapter:
    vendor_slug = "cursor"
    source_type = "api_sync"

    def can_handle(self, context: IngestionContext) -> bool:
        return context.vendor_slug == "cursor" and context.source_type == "api_sync"

    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]:
        return list(context.events)

    def extract_metadata(self, context: IngestionContext) -> dict:
        return dict(context.metadata)

    def requires_review(self) -> bool:
        return False
