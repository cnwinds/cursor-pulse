from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from pulse.util.timezone_ctx import DEFAULT_DISPLAY_TIMEZONE, display_zone

_UTC = ZoneInfo("UTC")


def _parse_datetime(value: datetime | str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace(" ", "T")
        if normalized.endswith("Z"):
            dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        elif "+" in normalized[10:] or normalized.endswith("+00:00"):
            dt = datetime.fromisoformat(normalized)
        else:
            dt = datetime.fromisoformat(normalized).replace(tzinfo=_UTC)
    else:
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
    return dt


def _to_display_zone(dt: datetime, *, tz: ZoneInfo | None = None) -> datetime:
    zone = tz or display_zone()
    return dt.astimezone(zone)


def format_display_datetime(value: datetime | str | None) -> str | None:
    """Format datetime as team display timezone (YYYY-MM-DD HH:MM:SS)."""
    dt = _parse_datetime(value) if value is not None else None
    if dt is None:
        return None
    return _to_display_zone(dt).strftime("%Y-%m-%d %H:%M:%S")


def format_display_datetime_iso(value: datetime | str | None) -> str | None:
    """Display timezone as ISO-8601 with numeric offset (for tools / APIs)."""
    dt = _parse_datetime(value) if value is not None else None
    if dt is None:
        return None
    return _to_display_zone(dt).isoformat(timespec="seconds")


def format_display_date(value: datetime | str | None) -> str | None:
    """Display timezone calendar date as YYYY-MM-DD."""
    wall = format_display_datetime(value)
    if wall is None:
        return None
    return wall[:10]


def format_data_updated_line(value: datetime | str | None) -> str:
    formatted = format_display_datetime(value)
    if formatted:
        return f"数据最后更新：{formatted}"
    return "数据最后更新：暂无"


def display_now_iso() -> str:
    """Current instant in display timezone as ISO-8601."""
    return format_display_datetime_iso(datetime.now(timezone.utc)) or ""


def serialize_datetime(value: datetime | str | None) -> str | None:
    """Serialize wall-clock instants for JSON/API (display timezone)."""
    return format_display_datetime_iso(value)


def serialize_date(value: date | None) -> str | None:
    """Serialize calendar dates without timezone conversion."""
    if value is None:
        return None
    return value.isoformat()


# Backward-compatible aliases (default TZ is Asia/Shanghai via settings).
format_china_datetime = format_display_datetime
format_china_datetime_iso = format_display_datetime_iso
format_china_date = format_display_date
china_now_iso = display_now_iso
tool_datetime = format_display_datetime_iso
