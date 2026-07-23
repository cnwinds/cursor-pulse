from __future__ import annotations

from pulse.config import AppConfig
from pulse.storage.repository import Repository
from pulse.tenant.service import resolve_team


def team_repository(session, config: AppConfig) -> tuple:
    team = resolve_team(session, config)
    return team, Repository(session, team.id)
