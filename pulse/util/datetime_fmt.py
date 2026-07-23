from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UTC = ZoneInfo("UTC")


def format_china_datetime(value: datetime | str | None) -> str | None:
    """Format API/SQLite datetime as China local time (YYYY-MM-DD HH:MM:SS)."""
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
    return dt.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def format_china_datetime_iso(value: datetime | str | None) -> str | None:
    """China local time as ISO-8601 with +08:00 (for LLM tool results)."""
    wall = format_china_datetime(value)
    if wall is None:
        return None
    return wall.replace(" ", "T") + "+08:00"


def format_data_updated_line(value: datetime | str | None) -> str:
    formatted = format_china_datetime(value)
    if formatted:
        return f"数据最后更新：{formatted}"
    return "数据最后更新：暂无"
