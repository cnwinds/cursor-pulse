from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pulse.util.datetime_fmt import serialize_datetime


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        # Naive values are treated as already-local wall time (legacy dumps).
        if value.tzinfo is None:
            return value.isoformat()
        return serialize_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps_json(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(value, indent=indent, ensure_ascii=False, default=json_default)
