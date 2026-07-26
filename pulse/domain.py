from __future__ import annotations

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


SubmitChannelLiteral = Literal["private", "group"]
