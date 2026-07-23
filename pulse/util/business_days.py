from __future__ import annotations

from datetime import date, timedelta

import chinese_calendar


def is_business_day(value: date) -> bool:
    """中国法定节假日与调休：chinese_calendar.is_workday。"""
    return bool(chinese_calendar.is_workday(value))


def first_business_day_of_month(year: int, month: int) -> date:
    candidate = date(year, month, 1)
    while not is_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def is_first_business_day(value: date) -> bool:
    return value == first_business_day_of_month(value.year, value.month)
