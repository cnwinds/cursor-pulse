from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WorkSchedule:
    timezone: str = "Asia/Shanghai"
    days: tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday=0 … Sunday=6
    start: str = "09:00"
    end: str = "18:00"

    def _parse_hm(self, value: str) -> time:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))

    def is_work_hours(self, now: datetime) -> bool:
        tz = ZoneInfo(self.timezone)
        local = now.astimezone(tz)
        if local.weekday() not in self.days:
            return False
        start = self._parse_hm(self.start)
        end = self._parse_hm(self.end)
        current = local.time()
        return start <= current <= end

    def off_hours_note(self) -> str:
        return (
            "当前是非工作时间。语气轻松简短，可说明天工作时段再细聊，"
            "但仍可接收 CSV 并做简单指引。"
        )
