from __future__ import annotations

from typing import Protocol

from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import UsageEventDTO


class IngestionAdapter(Protocol):
    vendor_slug: str | None
    source_type: str

    def can_handle(self, context: IngestionContext) -> bool: ...
    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]: ...
    def extract_metadata(self, context: IngestionContext) -> dict: ...
    def requires_review(self) -> bool: ...
