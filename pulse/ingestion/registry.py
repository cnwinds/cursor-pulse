from __future__ import annotations

from pulse.ingestion.adapters.cursor_api import CursorApiAdapter
from pulse.ingestion.adapters.manual_csv import ManualCsvAdapter
from pulse.ingestion.adapters.manual_text import ManualTextAdapter
from pulse.ingestion.adapters.manual_vision import ManualVisionAdapter
from pulse.ingestion.protocols import IngestionAdapter
from pulse.ingestion.types import IngestionContext

DEFAULT_ADAPTERS: list[IngestionAdapter] = [
    CursorApiAdapter(),
    ManualCsvAdapter(),
    ManualVisionAdapter(),
    ManualTextAdapter(),
]


def resolve_adapter(
    context: IngestionContext,
    adapters: list[IngestionAdapter] | None = None,
) -> IngestionAdapter:
    for adapter in adapters or DEFAULT_ADAPTERS:
        if adapter.can_handle(context):
            return adapter
    raise ValueError(f"no adapter for {context.vendor_slug}/{context.source_type}")
