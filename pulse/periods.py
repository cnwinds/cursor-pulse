from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pulse.config import AppConfig


def current_period(config: AppConfig, now: datetime | None = None) -> str:
    tz = ZoneInfo(config.collection.timezone)
    now = now or datetime.now(tz)
    return now.strftime(config.collection.period_format)
