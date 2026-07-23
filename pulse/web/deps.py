from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.tenant.context import team_repository
from pulse.web.auth_tokens import decode_access_token
from pulse.web.permissions import can_access_portal, has_permission, resolve_permissions


@dataclass
class PortalUser:
    member: Member
    permissions: set[str] = field(default_factory=set)


def _member_from_legacy_token(session: Session, config: AppConfig, token: str) -> Member | None:
    if not config.web.admin_token or token != config.web.admin_token:
        return None
    team, repo = team_repository(session, config)
    owners = [
        m
        for m in session.scalars(select(Member).where(Member.team_id == team.id)).all()
        if m.portal_role == "owner"
    ]
    from pulse.web.portal import ADMIN_LOGIN_USERNAME

    admin = next((m for m in owners if m.dingtalk_user_id == ADMIN_LOGIN_USERNAME), None)
    if admin:
        return admin
    if owners:
        return owners[0]
    if config.admin.dingtalk_user_ids:
        uid = config.admin.dingtalk_user_ids[0]
        return repo.get_or_create_member(uid, "Legacy Admin")
    return None


def get_portal_user(
    config: AppConfig,
    session: Session,
    authorization: str | None,
) -> PortalUser | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None

    try:
        payload = decode_access_token(config, token)
        member = session.get(Member, payload["sub"])
        if member and can_access_portal(member):
            return PortalUser(member=member, permissions=resolve_permissions(member))
    except Exception:
        pass

    legacy = _member_from_legacy_token(session, config, token)
    if legacy:
        if not legacy.portal_role:
            legacy.portal_role = "owner"
        if not legacy.portal_status:
            legacy.portal_status = "active"
        if legacy.status != "active":
            legacy.status = "active"
        return PortalUser(member=legacy, permissions=resolve_permissions(legacy))
    return None


def require_portal_user(
    config: AppConfig,
    session: Session,
    authorization: str | None = Header(default=None),
) -> PortalUser:
    user = get_portal_user(config, session, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或令牌无效")
    if not can_access_portal(user.member):
        raise HTTPException(status_code=403, detail="无后台访问权限")
    return user


def require_capability(capability: str):
    def _dep(
        config: AppConfig,
        session: Session,
        authorization: str | None = Header(default=None),
    ) -> PortalUser:
        user = require_portal_user(config, session, authorization)
        if not has_permission(user.member, capability):
            raise HTTPException(status_code=403, detail=f"缺少权限: {capability}")
        return user

    return _dep
