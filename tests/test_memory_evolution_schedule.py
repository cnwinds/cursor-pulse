from pulse.config import MemoryConfig, format_memory_evolution_cron


def test_memory_config_accepts_daily_evolution_day():
    cfg = MemoryConfig(evolution_day_of_week=-1)
    assert cfg.evolution_day_of_week == -1


def test_format_memory_evolution_cron_daily():
    assert format_memory_evolution_cron(-1, "03:15") == "每天 03:15"


def test_format_memory_evolution_cron_weekly():
    assert format_memory_evolution_cron(6, "02:00") == "周日 02:00"
