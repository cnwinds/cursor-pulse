from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
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


def period_last_day(period: str) -> date:
    year, month = map(int, period.split("-"))
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def period_end_datetime(period: str, timezone: str) -> datetime:
    """账期自然月最后一刻（含）。"""
    tz = ZoneInfo(timezone)
    last = period_last_day(period)
    return datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=tz)


def report_period_for_config(config: AppConfig, now: datetime | None = None) -> str:
    """月报/月结窗口使用的账期。"""
    tz = ZoneInfo(config.collection.timezone)
    now = now or datetime.now(tz)
    current = current_period(config, now)
    mode = getattr(config.collection, "report_period_mode", "previous")
    if mode == "previous":
        return previous_period(current)
    return current


def collection_period_for_config(config: AppConfig, now: datetime | None = None) -> str:
    """收集催办使用的账期（与月报账期对齐）。"""
    return report_period_for_config(config, now)
