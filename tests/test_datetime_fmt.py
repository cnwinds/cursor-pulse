from __future__ import annotations

from datetime import datetime, timezone

from pulse.util.datetime_fmt import (
    china_now_iso,
    format_china_date,
    format_china_datetime,
    format_data_updated_line,
    tool_datetime,
)


def test_format_china_datetime_from_utc():
    assert (
        format_china_datetime(datetime(2026, 7, 15, 4, 30, 0, tzinfo=timezone.utc))
        == "2026-07-15 12:30:00"
    )


def test_format_china_date_from_utc():
    assert (
        format_china_date(datetime(2026, 7, 23, 16, 0, 0, tzinfo=timezone.utc))
        == "2026-07-24"
    )
    assert format_china_date(None) is None


def test_format_china_datetime_iso_for_tools():
    from pulse.util.datetime_fmt import format_china_datetime_iso

    assert (
        format_china_datetime_iso(datetime(2026, 7, 22, 7, 15, 55, tzinfo=timezone.utc))
        == "2026-07-22T15:15:55+08:00"
    )
    assert format_china_datetime_iso(None) is None


def test_format_data_updated_line_without_value():
    assert format_data_updated_line(None) == "数据最后更新：暂无"


def test_china_now_iso_uses_offset():
    assert china_now_iso().endswith("+08:00")


def test_display_datetime_respects_timezone_context():
    from pulse.util.datetime_fmt import format_display_datetime_iso
    from pulse.util.timezone_ctx import activate_display_timezone

    with activate_display_timezone("America/New_York"):
        assert (
            format_display_datetime_iso(
                datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
            )
            == "2026-07-15T08:00:00-04:00"
        )


def test_tool_datetime_alias():
    assert (
        tool_datetime(datetime(2026, 7, 22, 7, 15, 55, tzinfo=timezone.utc))
        == "2026-07-22T15:15:55+08:00"
    )
