from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pulse.config import AppConfig


def previous_period(period: str) -> str:
    year, month = map(int, period.split("-"))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year:04d}-{month - 1:02d}"


def pct_change(old: float | int, new: float | int) -> float | None:
    if not old:
        return None
    return round((new - old) / old * 100, 2)


def current_period(config: AppConfig, now: datetime | None = None) -> str:
    tz = ZoneInfo(config.collection.timezone)
    now = now or datetime.now(tz)
    return now.strftime(config.collection.period_format)
