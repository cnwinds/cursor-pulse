from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.storage.repository import Repository
from pulse.web.passwords import hash_password


def sync_portal_owners_from_config(session: Session, team_id: str, admin_user_ids: list[str]) -> int:
    if not admin_user_ids:
        return 0
    updated = 0
    for uid in admin_user_ids:
        member = session.scalar(
            select(Member).where(Member.team_id == team_id, Member.dingtalk_user_id == uid)
        )
        if member is None:
            member = Member(
                team_id=team_id,
                dingtalk_user_id=uid,
                display_name=uid,
                status="active",
                portal_role="owner",
            )
            session.add(member)
            updated += 1
        elif member.portal_role != "owner":
            member.portal_role = "owner"
            if member.status != "active":
                member.status = "active"
            updated += 1
    return updated


def bootstrap_portal_owner(
    repo: Repository,
    *,
    dingtalk_user_id: str,
    display_name: str,
    password: str,
) -> Member:
    member = repo.get_or_create_member(dingtalk_user_id, display_name)
    member.status = "active"
    member.portal_role = "owner"
    member.password_hash = hash_password(password)
    member.last_portal_login_at = datetime.now(timezone.utc)
    return member


def grant_portal_role(
    session: Session,
    team_id: str,
    dingtalk_user_id: str,
    *,
    role: str,
    display_name: str = "",
    permissions: list[str] | None = None,
) -> Member:
    member = session.scalar(
        select(Member).where(Member.team_id == team_id, Member.dingtalk_user_id == dingtalk_user_id)
    )
    if member is None:
        member = Member(
            team_id=team_id,
            dingtalk_user_id=dingtalk_user_id,
            display_name=display_name or dingtalk_user_id,
            status="active",
        )
        session.add(member)
    member.portal_role = role
    member.portal_permissions = permissions if role == "custom" else None
    if display_name:
        member.display_name = display_name
    if member.status != "active":
        member.status = "active"
    session.flush()
    return member
