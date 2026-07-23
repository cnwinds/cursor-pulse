from __future__ import annotations

from datetime import datetime, timezone

from pulse.util.datetime_fmt import format_china_datetime, format_data_updated_line


def test_format_china_datetime_from_utc():
    assert (
        format_china_datetime(datetime(2026, 7, 15, 4, 30, 0, tzinfo=timezone.utc))
        == "2026-07-15 12:30:00"
    )


def test_format_china_datetime_iso_for_tools():
    from pulse.util.datetime_fmt import format_china_datetime_iso

    assert (
        format_china_datetime_iso(datetime(2026, 7, 22, 7, 15, 55, tzinfo=timezone.utc))
        == "2026-07-22T15:15:55+08:00"
    )
    assert format_china_datetime_iso(None) is None


def test_format_data_updated_line_without_value():
    assert format_data_updated_line(None) == "数据最后更新：暂无"
