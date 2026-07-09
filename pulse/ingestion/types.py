from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pulse.integrations.cursor_api import UsageEventDTO


@dataclass
class IngestionContext:
    account_id: str
    vendor_id: str
    vendor_slug: str
    billing_period: str
    member_id: str | None
    channel: str
    source_type: str
    triggered_by: str
    raw_file_path: Path | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[UsageEventDTO] = field(default_factory=list)


@dataclass
class IngestionResult:
    ingestion_id: str
    event_count: int
    status: str
