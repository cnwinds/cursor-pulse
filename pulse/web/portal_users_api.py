from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.util.datetime_fmt import serialize_datetime
from pulse.web.audit import log_admin_action
from pulse.web.deps import PortalUser
from pulse.web.permissions import (
    ALL_PERMISSIONS,
    PORTAL_ROLE_DESCRIPTIONS,
    PORTAL_ROLE_LABELS,
    ROLE_PERMISSIONS,
)
from pulse.web.schemas import (
    PortalApproveBody,
    PortalCreateUserBody,
    PortalLinkIdentityBody,
    PortalSetPasswordBody,
)


def _portal_user_row(
    member: Member,
    session: Session | None = None,
    *,
    identities: list[dict] | None = None,
) -> dict:
    has_password = bool(member.password_hash)
    if identities is None:
        identities = []
        if session is not None:
            from pulse.identity.service import identities_payload

            identities = identities_payload(session, member)
    return {
        "id": member.id,
        "display_name": member.display_name,
        "channel_user_id": member.channel_user_id,
        "channel": getattr(member, "channel", None) or "web",
        "identities": identities,
        "has_password": has_password,
        "portal_status": member.portal_status,
        "portal_role": member.portal_role,
        "portal_permissions": member.portal_permissions,
        "last_portal_login_at": serialize_datetime(member.last_portal_login_at),
        "created_at": serialize_datetime(member.created_at),
    }


def _portal_user_rows(session: Session, members: list[Member]) -> list[dict]:
    by_member: dict[str, list[dict]] = {m.id: [] for m in members}
    if members:
        from sqlalchemy import select

        from pulse.storage.models import MemberIdentity

        ids = [m.id for m in members]
        rows = session.scalars(
            select(MemberIdentity)
            .where(MemberIdentity.member_id.in_(ids))
            .order_by(MemberIdentity.channel.asc(), MemberIdentity.external_id.asc())
        ).all()
        for row in rows:
            by_member.setdefault(row.member_id, []).append(
                {"channel": row.channel, "external_id": row.external_id}
            )
    return [
        _portal_user_row(m, session, identities=by_member.get(m.id, [])) for m in members
    ]


def _portal_directory_row(member: Member) -> dict:
    return {
        "id": member.id,
        "display_name": member.display_name,
        "channel_user_id": member.channel_user_id,
        "channel": getattr(member, "channel", None) or "dingtalk",
        "department_name": member.department_name,
        "portal_status": member.portal_status,
    }


def register_portal_users_routes(app, config: AppConfig, get_db, require_capability, team_repo_fn):
    @app.get("/api/portal/roles", dependencies=[Depends(require_capability("admin:users"))])
    def portal_roles():
        return [
            {
                "id": role,
                "label": PORTAL_ROLE_LABELS.get(role, role),
                "description": PORTAL_ROLE_DESCRIPTIONS.get(role, ""),
                "permissions": sorted(ROLE_PERMISSIONS.get(role, frozenset())),
            }
            for role in ("owner", "operator", "auditor", "ai_member", "custom")
        ]

    @app.get(
        "/api/portal/users/pending",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_pending_users(session: Session = Depends(get_db)):
        from pulse.web.portal import list_pending_portal_users

        team, _ = team_repo_fn(session)
        return _portal_user_rows(session, list_pending_portal_users(session, team.id))

    @app.get(
        "/api/portal/users",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_active_users(session: Session = Depends(get_db)):
        from pulse.web.portal import list_portal_users

        team, _ = team_repo_fn(session)
        return _portal_user_rows(session, list_portal_users(session, team.id))

    @app.post(
        "/api/portal/users",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_create_user(
        body: PortalCreateUserBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, create_local_portal_user

        if body.portal_role not in ROLE_PERMISSIONS and body.portal_role != "custom":
            raise HTTPException(400, detail="无效的 portal_role")
        perms = None
        if body.portal_role == "custom":
            perms = [p for p in (body.portal_permissions or []) if p in ALL_PERMISSIONS]
        team, _ = team_repo_fn(session)
        try:
            member = create_local_portal_user(
                session,
                team.id,
                username=body.username,
                display_name=body.display_name,
                password=body.password,
                role=body.portal_role,
                permissions=perms,
            )
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.create",
            capability="admin:users",
            detail=f"{body.username} -> {body.portal_role}",
        )
        session.commit()
        return _portal_user_row(member, session)

    @app.get(
        "/api/portal/users/directory-search",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_directory_search(
        q: str = Query(..., min_length=1, max_length=64),
        session: Session = Depends(get_db),
    ):
        from pulse.integrations.dingtalk_directory import (
            make_directory_client,
            search_directory_by_name,
        )
        from pulse.web.portal import search_local_directory_members

        team, repo = team_repo_fn(session)
        local_members = search_local_directory_members(session, team.id, q)
        if local_members:
            return [_portal_directory_row(m) for m in local_members]

        try:
            client = make_directory_client(config)
            matches = search_directory_by_name(
                client,
                q,
                root_dept_id=config.dingtalk.sync_root_dept_id,
            )
        except Exception as exc:
            raise HTTPException(400, detail=f"搜索通讯录失败：{exc}") from exc

        rows: list[dict] = []
        for user in matches:
            userid = str(user.get("userid") or "")
            name = user.get("name") or userid
            dept_name = user.get("department_name")
            member = repo.get_or_create_member(userid, name, channel="dingtalk")
            member.display_name = name
            if dept_name:
                member.department_name = dept_name
            member.employment_status = "active"
            rows.append(_portal_directory_row(member))
        session.commit()
        return rows

    @app.get(
        "/api/portal/users/directory-tree",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_directory_tree(
        dept_id: int | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        from pulse.integrations.dingtalk_directory import (
            list_directory_tree_children,
            make_directory_client,
        )

        team, repo = team_repo_fn(session)
        root_dept = dept_id if dept_id is not None else config.dingtalk.sync_root_dept_id
        try:
            client = make_directory_client(config)
            tree = list_directory_tree_children(repo, client, root_dept)
        except Exception as exc:
            raise HTTPException(400, detail=f"加载组织架构失败：{exc}") from exc
        session.commit()
        return tree

    @app.get(
        "/api/portal/users/directory-candidates",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_directory_candidates(session: Session = Depends(get_db)):
        from pulse.web.portal import list_directory_portal_candidates

        team, _ = team_repo_fn(session)
        return [_portal_directory_row(m) for m in list_directory_portal_candidates(session, team.id)]

    @app.post(
        "/api/portal/users/sync-directory",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_sync_directory(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.integrations.dingtalk_directory import sync_dingtalk_directory
        from pulse.web.portal import list_directory_portal_candidates

        team, repo = team_repo_fn(session)
        try:
            stats = sync_dingtalk_directory(repo, config)
        except Exception as exc:
            raise HTTPException(400, detail=f"同步通讯录失败：{exc}") from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="dingtalk.directory_sync",
            capability="admin:users",
            detail=f"portal_users fetched={stats.get('fetched', 0)}",
        )
        session.commit()
        candidates = list_directory_portal_candidates(session, team.id)
        return {
            "stats": stats,
            "candidates": [_portal_directory_row(m) for m in candidates],
        }

    @app.post(
        "/api/portal/users/{member_id}/identities",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_link_identity(
        member_id: str,
        body: PortalLinkIdentityBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.identity.service import IdentityError, link_identity, resolve_member

        team, _ = team_repo_fn(session)
        member = session.get(Member, member_id)
        if member is None or member.team_id != team.id:
            raise HTTPException(404, detail="成员不存在")
        drop_snapshot = None
        if body.merge:
            other = resolve_member(
                session, team.id, channel=body.channel, external_id=body.external_id
            )
            if other is not None and other.id != member.id:
                drop_snapshot = (
                    f"drop={other.id}/{other.display_name}/role={other.portal_role}"
                )
        try:
            member = link_identity(
                session,
                member,
                channel=body.channel,
                external_id=body.external_id,
                merge_if_taken=body.merge,
                actor=user.member,
            )
        except IdentityError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        detail = f"{member.id} + {body.channel}:{body.external_id}"
        if body.merge:
            detail += f" merge {drop_snapshot or ''} -> role={member.portal_role}"
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.link_identity",
            capability="admin:users",
            detail=detail,
        )
        session.commit()
        return _portal_user_row(member, session)

    @app.put(
        "/api/portal/users/{member_id}/password",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_set_password(
        member_id: str,
        body: PortalSetPasswordBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.identity.service import (
            IdentityError,
            ensure_identity,
            link_identity,
            list_identities,
            set_member_password,
        )

        team, _ = team_repo_fn(session)
        member = session.get(Member, member_id)
        if member is None or member.team_id != team.id:
            raise HTTPException(404, detail="成员不存在")
        has_web = any(i.channel == "web" for i in list_identities(session, member.id))
        if not has_web:
            username = (body.username or "").strip()
            if not username and member.channel == "web":
                username = member.channel_user_id
            if not username:
                raise HTTPException(
                    400,
                    detail="请提供 username，为该用户创建 Web 登录名后再设密码",
                )
            try:
                link_identity(
                    session, member, channel="web", external_id=username, merge_if_taken=False
                )
            except IdentityError as exc:
                raise HTTPException(400, detail=str(exc)) from exc
        elif member.channel == "web":
            try:
                ensure_identity(
                    session, member, channel="web", external_id=member.channel_user_id
                )
            except IdentityError:
                pass
        try:
            set_member_password(session, member, body.password)
        except IdentityError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.set_password",
            capability="admin:users",
            detail=member.id,
        )
        session.commit()
        return _portal_user_row(member, session)

    @app.post(
        "/api/portal/users/{member_id}/approve",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_approve_user(
        member_id: str,
        body: PortalApproveBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, approve_portal_user

        if body.portal_role not in ROLE_PERMISSIONS and body.portal_role != "custom":
            raise HTTPException(400, detail="无效的 portal_role")
        perms = None
        if body.portal_role == "custom":
            perms = [p for p in (body.portal_permissions or []) if p in ALL_PERMISSIONS]
        team, _ = team_repo_fn(session)
        try:
            member = approve_portal_user(
                session,
                team.id,
                member_id,
                role=body.portal_role,
                permissions=perms,
            )
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.approve",
            capability="admin:users",
            detail=f"{member.channel_user_id} -> {member.portal_role}",
        )
        session.commit()
        return _portal_user_row(member, session)

    @app.post(
        "/api/portal/users/{member_id}/reject",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_reject_user(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, reject_portal_user

        team, _ = team_repo_fn(session)
        try:
            member = reject_portal_user(session, team.id, member_id)
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.reject",
            capability="admin:users",
            detail=member.channel_user_id,
        )
        session.commit()
        return _portal_user_row(member, session)

    @app.post(
        "/api/portal/users/{member_id}/disable",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_disable_user(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, disable_portal_user

        team, _ = team_repo_fn(session)
        if member_id == user.member.id:
            raise HTTPException(400, detail="不能禁用当前登录账号")
        try:
            member = disable_portal_user(session, team.id, member_id)
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.disable",
            capability="admin:users",
            detail=member.channel_user_id,
        )
        session.commit()
        return _portal_user_row(member, session)

    @app.delete(
        "/api/portal/users/{member_id}",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_delete_user(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, delete_member_by_id

        team, _ = team_repo_fn(session)
        member = session.get(Member, member_id)
        if member is None or member.team_id != team.id:
            raise HTTPException(404, detail="成员不存在")
        if member_id == user.member.id:
            raise HTTPException(400, detail="不能删除当前登录账号")
        try:
            deleted = delete_member_by_id(session, team.id, member_id)
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.delete",
            capability="admin:users",
            detail=deleted.channel_user_id,
        )
        session.commit()
        return {"ok": True, "id": member_id}
