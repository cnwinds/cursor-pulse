from __future__ import annotations

from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import UsageEventDTO


class ManualVisionAdapter:
    """Wraps vendor screenshot OCR; events/metadata are pre-filled on context after vision extract."""

    vendor_slug = None
    source_type = "manual_vision"

    def can_handle(self, context: IngestionContext) -> bool:
        return context.source_type == "manual_vision" and context.vendor_slug != "cursor"

    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]:
        return list(context.events)

    def extract_metadata(self, context: IngestionContext) -> dict:
        return dict(context.metadata)

    def requires_review(self) -> bool:
        return True
