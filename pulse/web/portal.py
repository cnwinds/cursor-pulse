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


def get_team_member(session: Session, team_id: str, dingtalk_user_id: str) -> Member | None:
    return session.scalar(
        select(Member).where(Member.team_id == team_id, Member.dingtalk_user_id == dingtalk_user_id)
    )


def sync_portal_owners_from_config(session: Session, team_id: str, admin_user_ids: list[str]) -> int:
    if not admin_user_ids:
        return 0
    updated = 0
    for uid in admin_user_ids:
        member = get_team_member(session, team_id, uid)
        if member is None:
            member = Member(
                team_id=team_id,
                dingtalk_user_id=uid,
                display_name=uid,
                status="active",
                portal_status="active",
                portal_role="owner",
            )
            session.add(member)
            updated += 1
        elif member.portal_role != "owner":
            member.portal_role = "owner"
            member.portal_status = "active"
            if member.status != "active":
                member.status = "active"
            updated += 1
    return updated


def reconcile_oauth_member(
    repo: Repository,
    *,
    enterprise_userid: str,
    display_name: str,
) -> Member | None:
    """将 OAuth 登录对齐到通讯录 userid，并清理历史 openId 重复账号。"""
    member = repo.get_member_by_dingtalk_id(enterprise_userid)
    if member is not None:
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
    legacy = [row for row in rows if looks_like_open_id(row.dingtalk_user_id)]
    if len(legacy) != 1:
        return None
    legacy[0].dingtalk_user_id = enterprise_userid
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
        if looks_like_open_id(row.dingtalk_user_id) and not row.ingestions:
            repo.session.delete(row)
    repo.session.flush()


def ensure_admin_member(repo: Repository) -> Member:
    member = repo.get_or_create_member(ADMIN_LOGIN_USERNAME, ADMIN_DISPLAY_NAME)
    member.status = "active"
    member.portal_status = "active"
    member.portal_role = "owner"
    return member


def bootstrap_portal_owner(
    repo: Repository,
    *,
    dingtalk_user_id: str,
    display_name: str,
    password: str,
) -> Member:
    member = repo.get_or_create_member(dingtalk_user_id, display_name)
    member.status = "active"
    member.portal_status = "active"
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
    member = get_team_member(session, team_id, dingtalk_user_id)
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
    dingtalk_user_id: str,
) -> Member:
    member = get_team_member(session, team_id, dingtalk_user_id)
    if member is None:
        raise PortalAdminError(f"未找到成员: {dingtalk_user_id}")
    if not member.portal_role and not member.password_hash:
        raise PortalAdminError(f"{dingtalk_user_id} 无后台权限可撤销")
    member.portal_role = None
    member.portal_permissions = None
    member.portal_status = None
    member.password_hash = None
    member.last_portal_login_at = None
    session.flush()
    return member


def delete_member_without_ingestions(
    session: Session,
    team_id: str,
    dingtalk_user_id: str,
) -> Member:
    member = get_team_member(session, team_id, dingtalk_user_id)
    if member is None:
        raise PortalAdminError(f"未找到成员: {dingtalk_user_id}")
    if member.ingestions:
        raise PortalAdminError(
            f"{dingtalk_user_id} 有 {len(member.ingestions)} 条摄取记录，无法删除"
        )
    session.delete(member)
    session.flush()
    return member


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
                Member.dingtalk_user_id != ADMIN_LOGIN_USERNAME,
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
                Member.dingtalk_user_id != ADMIN_LOGIN_USERNAME,
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
