from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import Member
from pulse.storage.repository import Repository
from pulse.web.passwords import hash_password
from pulse.web.dingtalk_oauth import looks_like_open_id


ADMIN_LOGIN_USERNAME = "admin"
ADMIN_DISPLAY_NAME = "超级管理员"


class PortalAdminError(RuntimeError):
    pass


def identity_channel_for_config(bot_name: str | None) -> str:
    """Map BOT_PLATFORM to Member.channel for admin provisioning."""
    from pulse.channels.base import normalize_platform

    platform = normalize_platform(bot_name)
    if platform in ("dingtalk", "feishu"):
        return platform
    return "web"


def get_team_member(
    session: Session,
    team_id: str,
    channel_user_id: str,
    *,
    channel: str | None = None,
) -> Member | None:
    from pulse.identity.service import resolve_member

    if channel is not None:
        return resolve_member(
            session, team_id, channel=channel, external_id=channel_user_id
        )
    for preferred in ("web", "dingtalk", "feishu"):
        member = resolve_member(
            session, team_id, channel=preferred, external_id=channel_user_id
        )
        if member is not None:
            return member
    return session.scalar(
        select(Member).where(
            Member.team_id == team_id,
            Member.channel_user_id == channel_user_id,
        )
    )


def sync_portal_owners_from_config(
    session: Session,
    team_id: str,
    admin_user_ids: list[str],
    *,
    channel: str = "web",
) -> int:
    if not admin_user_ids:
        return 0
    updated = 0
    for uid in admin_user_ids:
        # Prefer exact (channel, userid); fall back to any existing row for that userid
        # so IM admins are not duplicated as channel=web.
        member = get_team_member(session, team_id, uid, channel=channel)
        if member is None:
            member = get_team_member(session, team_id, uid)
        if member is None:
            member = Member(
                team_id=team_id,
                channel=channel,
                channel_user_id=uid,
                display_name=uid,
                status="active",
                portal_status="active",
                portal_role="owner",
            )
            session.add(member)
            session.flush()
            from pulse.identity.service import ensure_identity

            ensure_identity(session, member, channel=channel, external_id=uid)
            updated += 1
        elif member.portal_role != "owner":
            member.portal_role = "owner"
            member.portal_status = "active"
            if member.status != "active":
                member.status = "active"
            updated += 1
    session.flush()
    return updated


def reconcile_oauth_member(
    repo: Repository,
    *,
    enterprise_userid: str,
    display_name: str,
) -> Member | None:
    """将 OAuth 登录对齐到通讯录 userid，并清理历史 openId 重复账号。"""
    member = repo.get_member_by_channel_user_id(enterprise_userid, channel="dingtalk")
    if member is None:
        member = repo.get_member_by_channel_user_id(enterprise_userid)
    if member is not None:
        member.channel = member.channel or "dingtalk"
        _cleanup_legacy_oauth_duplicates(repo, display_name=display_name, keep_id=member.id)
        return member

    rows = list(
        repo.session.scalars(
            select(Member).where(
                Member.team_id == repo.team_id,
                Member.display_name == display_name,
            )
        ).all()
    )
    legacy = [row for row in rows if looks_like_open_id(row.channel_user_id)]
    if len(legacy) != 1:
        return None
    legacy[0].channel_user_id = enterprise_userid
    repo.session.flush()
    return legacy[0]


def _cleanup_legacy_oauth_duplicates(
    repo: Repository,
    *,
    display_name: str,
    keep_id: str,
) -> None:
    rows = list(
        repo.session.scalars(
            select(Member).where(
                Member.team_id == repo.team_id,
                Member.display_name == display_name,
            )
        ).all()
    )
    for row in rows:
        if row.id == keep_id:
            continue
        if looks_like_open_id(row.channel_user_id) and not row.ingestions:
            repo.session.delete(row)
    repo.session.flush()


def ensure_admin_member(repo: Repository) -> Member:
    from pulse.identity.service import ensure_identity, refresh_member_primary_cache

    member = repo.get_or_create_member(
        ADMIN_LOGIN_USERNAME, ADMIN_DISPLAY_NAME, channel="web"
    )
    member.channel = "web"
    member.status = "active"
    member.portal_status = "active"
    member.portal_role = "owner"
    ensure_identity(
        repo.session, member, channel="web", external_id=ADMIN_LOGIN_USERNAME
    )
    refresh_member_primary_cache(repo.session, member)
    return member


def bootstrap_portal_owner(
    repo: Repository,
    *,
    channel_user_id: str,
    display_name: str,
    password: str,
    channel: str = "web",
) -> Member:
    from pulse.identity.service import ensure_identity, refresh_member_primary_cache

    member = repo.get_or_create_member(channel_user_id, display_name, channel=channel)
    member.channel = channel
    member.status = "active"
    member.portal_status = "active"
    member.portal_role = "owner"
    member.password_hash = hash_password(password)
    member.last_portal_login_at = datetime.now(timezone.utc)
    ensure_identity(
        repo.session, member, channel=channel, external_id=channel_user_id
    )
    refresh_member_primary_cache(repo.session, member)
    return member


def grant_portal_role(
    session: Session,
    team_id: str,
    channel_user_id: str,
    *,
    role: str,
    display_name: str = "",
    permissions: list[str] | None = None,
) -> Member:
    from pulse.identity.service import ensure_identity

    member = get_team_member(session, team_id, channel_user_id, channel="web")
    if member is None:
        member = get_team_member(session, team_id, channel_user_id)
    if member is None:
        member = Member(
            team_id=team_id,
            channel="web",
            channel_user_id=channel_user_id,
            display_name=display_name or channel_user_id,
            status="active",
        )
        session.add(member)
        session.flush()
        ensure_identity(
            session, member, channel="web", external_id=channel_user_id
        )
    member.portal_role = role
    member.portal_permissions = permissions if role == "custom" else None
    member.portal_status = "active"
    if display_name:
        member.display_name = display_name
    if member.status != "active":
        member.status = "active"
    session.flush()
    return member


def revoke_portal_access(
    session: Session,
    team_id: str,
    channel_user_id: str,
) -> Member:
    member = get_team_member(session, team_id, channel_user_id)
    if member is None:
        raise PortalAdminError(f"未找到成员: {channel_user_id}")
    if not member.portal_role and not member.password_hash:
        raise PortalAdminError(f"{channel_user_id} 无后台权限可撤销")
    member.portal_role = None
    member.portal_permissions = None
    member.portal_status = None
    member.password_hash = None
    member.last_portal_login_at = None
    session.flush()
    return member


def delete_member_by_id(
    session: Session,
    team_id: str,
    member_id: str,
) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.team_id != team_id:
        raise PortalAdminError("成员不存在")
    label = member.channel_user_id or member.display_name or member.id
    if member.ingestions:
        raise PortalAdminError(f"{label} 有 {len(member.ingestions)} 条摄取记录，无法删除")
    from pulse.identity.service import list_identities

    for identity in list_identities(session, member.id):
        session.delete(identity)
    session.flush()
    session.delete(member)
    session.flush()
    return member


def delete_member_without_ingestions(
    session: Session,
    team_id: str,
    channel_user_id: str,
) -> Member:
    member = get_team_member(session, team_id, channel_user_id)
    if member is None:
        raise PortalAdminError(f"未找到成员: {channel_user_id}")
    return delete_member_by_id(session, team_id, member.id)


delete_member_without_submissions = delete_member_without_ingestions


def list_pending_portal_users(session: Session, team_id: str) -> list[Member]:
    return list(
        session.scalars(
            select(Member)
            .where(Member.team_id == team_id, Member.portal_status == "pending")
            .order_by(Member.created_at.desc())
        ).all()
    )


def list_directory_portal_candidates(session: Session, team_id: str) -> list[Member]:
    """通讯录已同步、尚未开通后台的成员（不含待审批/已开通/已禁用）。"""
    return list(
        session.scalars(
            select(Member)
            .where(
                Member.team_id == team_id,
                Member.channel_user_id != ADMIN_LOGIN_USERNAME,
                Member.portal_status.is_(None)
                | (Member.portal_status == "rejected"),
                Member.department_name.is_not(None),
            )
            .order_by(Member.display_name)
        ).all()
    )


def search_local_directory_members(
    session: Session,
    team_id: str,
    query: str,
    *,
    limit: int = 50,
) -> list[Member]:
    """在已同步到本地的通讯录成员中按姓名搜索。"""
    q = query.strip()
    if not q:
        return []
    pattern = f"%{q}%"
    return list(
        session.scalars(
            select(Member)
            .where(
                Member.team_id == team_id,
                Member.channel_user_id != ADMIN_LOGIN_USERNAME,
                Member.department_name.is_not(None),
                Member.display_name.like(pattern),
            )
            .order_by(Member.display_name)
            .limit(limit)
        ).all()
    )


def list_portal_users(session: Session, team_id: str) -> list[Member]:
    return list(
        session.scalars(
            select(Member)
            .where(
                Member.team_id == team_id,
                Member.portal_status.in_(("active", "disabled")),
            )
            .order_by(Member.display_name)
        ).all()
    )


def create_local_portal_user(
    session: Session,
    team_id: str,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    permissions: list[str] | None = None,
) -> Member:
    from pulse.identity.service import (
        IdentityError,
        ensure_identity,
        resolve_member,
        set_member_password,
        validate_web_username,
    )

    try:
        username = validate_web_username(username)
    except IdentityError as exc:
        raise PortalAdminError(str(exc)) from exc
    if username.lower() == ADMIN_LOGIN_USERNAME:
        raise PortalAdminError(f"用户名 {ADMIN_LOGIN_USERNAME} 为系统保留名")
    if resolve_member(session, team_id, channel="web", external_id=username):
        raise PortalAdminError(f"用户名已存在: {username}")
    name = (display_name or "").strip() or username
    member = Member(
        team_id=team_id,
        channel="web",
        channel_user_id=username,
        display_name=name,
        status="active",
        portal_status="active",
        portal_role=role,
        portal_permissions=permissions if role == "custom" else None,
    )
    session.add(member)
    session.flush()
    ensure_identity(session, member, channel="web", external_id=username)
    try:
        set_member_password(session, member, password)
    except IdentityError as exc:
        raise PortalAdminError(str(exc)) from exc
    return member


def approve_portal_user(
    session: Session,
    team_id: str,
    member_id: str,
    *,
    role: str,
    permissions: list[str] | None = None,
) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.team_id != team_id:
        raise PortalAdminError("成员不存在")
    if member.portal_status not in ("pending", "disabled", "rejected", "active", None):
        raise PortalAdminError("该用户不在可审批状态")
    member.portal_role = role
    member.portal_permissions = permissions if role == "custom" else None
    member.portal_status = "active"
    member.status = "active"
    session.flush()
    return member


def reject_portal_user(session: Session, team_id: str, member_id: str) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.team_id != team_id:
        raise PortalAdminError("成员不存在")
    if member.portal_status != "pending":
        raise PortalAdminError("仅待审批用户可拒绝")
    member.portal_status = "rejected"
    member.portal_role = None
    member.portal_permissions = None
    session.flush()
    return member


def disable_portal_user(session: Session, team_id: str, member_id: str) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.team_id != team_id:
        raise PortalAdminError("成员不存在")
    if member.portal_status != "active":
        raise PortalAdminError("仅已开通用户可禁用")
    member.portal_status = "disabled"
    session.flush()
    return member
