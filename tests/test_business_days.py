from datetime import date

from pulse.util.business_days import (
    first_business_day_of_month,
    is_business_day,
    is_first_business_day,
)


def test_first_business_day_of_month_skips_weekend():
    # 2026-02-01 is Sunday
    assert first_business_day_of_month(2026, 2) == date(2026, 2, 2)


def test_is_first_business_day():
    assert is_first_business_day(date(2026, 2, 2)) is True
    assert is_first_business_day(date(2026, 2, 3)) is False


def test_is_business_day_weekday():
    assert is_business_day(date(2026, 7, 15)) is True
