from __future__ import annotations

from personamem.persona import Persona
from personamem.schedule import WorkSchedule
from pulse.config import AppConfig, PersonaConfig


def persona_from_config(cfg: PersonaConfig | AppConfig) -> Persona:
    if isinstance(cfg, AppConfig):
        persona_cfg = cfg.persona
        tz = cfg.collection.timezone
    else:
        persona_cfg = cfg
        tz = "Asia/Shanghai"

    schedule = WorkSchedule(
        timezone=tz,
        days=tuple(persona_cfg.work_days),
        start=persona_cfg.work_start,
        end=persona_cfg.work_end,
    )
    return Persona(
        name=persona_cfg.name,
        role=persona_cfg.role,
        tone=persona_cfg.tone,
        work_hours=persona_cfg.work_hours,
        schedule=schedule,
    )
