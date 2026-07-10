from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal


class CostRaw(str, Enum):
    INCLUDED = "included"
    FREE = "free"
    NONE = "none"
    USAGE_BASED = "usage_based"


class SubmitChannel(str, Enum):
    PRIVATE = "private"
    GROUP = "group"


@dataclass(frozen=True)
class UsageEventRecord:
    event_at: datetime
    event_date: date
    kind: str
    model: str
    max_mode: bool
    tokens_input_cache_write: int
    tokens_input_no_cache: int
    tokens_cache_read: int
    tokens_output: int
    tokens_total: int
    cost_raw: CostRaw
    cost_usd: Decimal
    cloud_agent_id: str | None
    automation_id: str | None
    source_row_hash: str


@dataclass(frozen=True)
class ParseSummary:
    period_hint: str | None
    date_min: date
    date_max: date
    event_count: int
    total_tokens: int
    total_cost_usd: Decimal
    top_models: list[tuple[str, int]]
    all_included_or_free: bool


@dataclass(frozen=True)
class ParsedCsv:
    records: list[UsageEventRecord]
    summary: ParseSummary


SubmitChannelLiteral = Literal["private", "group"]

COMPUTATION_VERSION = "aggregate-v2"
