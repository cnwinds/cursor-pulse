from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import Team


def make_team(session: Session, slug: str = "test") -> Team:
    team = session.scalar(select(Team).where(Team.slug == slug))
    if team is None:
        team = Team(slug=slug, name=slug.title())
        session.add(team)
        session.flush()
    return team


def make_team_repo(session: Session, slug: str = "test"):
    team = make_team(session, slug)
    try:
        from pulse.storage.repository import Repository

        return team, Repository(session, team.id)
    except ImportError:
        return team, None
