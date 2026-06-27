from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import Member, Team


def get_or_create_team(session: Session, slug: str, *, name: str | None = None) -> Team:
    team = session.scalar(select(Team).where(Team.slug == slug))
    if team:
        return team
    team = Team(slug=slug, name=name or slug)
    session.add(team)
    session.flush()
    return team


def resolve_team(session: Session, config: AppConfig) -> Team:
    slug = config.tenant.slug
    team = get_or_create_team(session, slug, name=config.tenant.name or slug)
    _backfill_team_id(session, team.id)
    if config.admin.dingtalk_user_ids:
        from pulse.web.portal import sync_portal_owners_from_config

        sync_portal_owners_from_config(session, team.id, config.admin.dingtalk_user_ids)
        session.flush()
    return team


def _backfill_team_id(session: Session, team_id: str) -> None:
    """将历史数据挂到默认团队（幂等）。"""
    members = session.scalars(select(Member).where(Member.team_id.is_(None))).all()
    for member in members:
        member.team_id = team_id
    session.flush()
