from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from personamem.persona import Persona
from personamem.schedule import WorkSchedule


def test_off_hours_detection():
    sched = WorkSchedule(timezone="Asia/Shanghai", days=(0, 1, 2, 3, 4), start="09:00", end="18:00")
    # Saturday 20:00 CST
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)  # Saturday evening in Shanghai
    assert sched.is_work_hours(now) is False

    persona = Persona(schedule=sched)
    note = persona.schedule_note(now)
    assert note is not None


def test_work_hours_weekday():
    sched = WorkSchedule(timezone="Asia/Shanghai", days=(0, 1, 2, 3, 4), start="09:00", end="18:00")
    # Monday 10:00 Shanghai
    now = datetime(2026, 6, 29, 2, 0, tzinfo=timezone.utc)
    assert sched.is_work_hours(now) is True
