from __future__ import annotations

import os
from pathlib import Path


def pulse_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    candidates = [
        Path.cwd() / "data" / "pulse.db",
        Path(__file__).resolve().parents[1] / "data" / "pulse.db",
    ]
    for path in candidates:
        if path.is_file():
            return f"sqlite:///{path.as_posix()}"
    return "sqlite:///data/pulse.db"


def read_team_setting_section(
    *,
    team_slug: str,
    section: str,
    database_url: str | None = None,
) -> dict:
    if not team_slug:
        return {}
    url = database_url or pulse_database_url()
    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from pulse.storage.models import Team, TeamSetting

        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine = create_engine(url, connect_args=connect_args)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with session_factory() as session:
            team = session.scalar(select(Team).where(Team.slug == team_slug).limit(1))
            if not team:
                return {}
            row = session.scalar(
                select(TeamSetting).where(
                    TeamSetting.team_id == team.id,
                    TeamSetting.section == section,
                )
            )
            if not row or not row.data:
                return {}
            return dict(row.data)
    except Exception:
        return {}
