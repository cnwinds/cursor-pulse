from __future__ import annotations

from datetime import date

import pytest

from pulse.tool_center.billing_cycle import (
    billing_cycle_containing,
    billing_cycle_for_period,
    period_last_day,
)


def test_billing_cycle_containing_mid_month():
    start, end = billing_cycle_containing(date(2026, 6, 30), date(2026, 7, 24))
    assert start == date(2026, 6, 24)
    assert end == date(2026, 7, 24)


def test_billing_cycle_for_period_june():
    start, end = billing_cycle_for_period(date(2026, 7, 24), "2026-06")
    assert start == date(2026, 6, 24)
    assert end == date(2026, 7, 24)


def test_period_last_day():
    assert period_last_day("2026-06") == date(2026, 6, 30)
    assert period_last_day("2026-02") == date(2026, 2, 28)
