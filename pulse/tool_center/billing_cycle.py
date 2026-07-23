from __future__ import annotations

import calendar
from datetime import date, timedelta


def period_first_day(period: str) -> date:
    year, month = map(int, period.split("-"))
    return date(year, month, 1)


def period_last_day(period: str) -> date:
    year, month = map(int, period.split("-"))
    last = calendar.monthrange(year, month)[1]
    return date(year, month, last)


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def billing_cycle_containing(ref_date: date, next_reset_on: date) -> tuple[date, date]:
    """返回包含 ref_date 的订阅周期 [start, end)。

    next_reset_on 为当前周期结束日（下次用量重置日）。
    """
    end = next_reset_on
    start = add_months(end, -1)
    while ref_date < start:
        end = start
        start = add_months(end, -1)
    while ref_date >= end:
        start = end
        end = add_months(end, 1)
    return start, end


def billing_cycle_for_period(next_reset_on: date, period: str) -> tuple[date, date]:
    """按自然月最后一天定位该月对应的订阅周期。"""
    return billing_cycle_containing(period_last_day(period), next_reset_on)


def format_cycle_range(start: date, end: date) -> str:
    """人类可读的周期范围（含首尾日）。"""
    return f"{start.isoformat()} ~ {(end - timedelta(days=1)).isoformat()}"
