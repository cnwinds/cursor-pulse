from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import Team
from pulse.storage.repository import Repository


def make_team_repo(session: Session, slug: str = "test") -> tuple[Team, Repository]:
    team = session.scalar(select(Team).where(Team.slug == slug))
    if team is None:
        team = Team(slug=slug, name=slug.title())
        session.add(team)
        session.flush()
    return team, Repository(session, team.id)
