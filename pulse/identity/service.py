"""Resolve and manage Member ↔ channel identities (web / dingtalk / feishu)."""

from __future__ import annotations

import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from pulse.storage.models import (
    AiAccount,
    AiAccountMember,
    Base,
    Member,
    MemberIdentity,
)

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{2,64}$")
_RESERVED_WEB_USERNAMES = frozenset({"admin"})

# Denormalized Member.channel_user_id is used for IM outbound addressing, so
# prefer IM identities when present; web-only members keep channel=web.
_PRIMARY_CHANNEL_ORDER = ("dingtalk", "feishu", "web")

# Tables/columns that reference members.id and must be rewritten on merge.
_MEMBER_FK_UPDATES: tuple[tuple[str, str], ...] = (
    ("members", "manager_member_id"),
    ("usage_ingestions", "member_id"),
    ("usage_records", "member_id"),
    ("alert_logs", "member_id"),
    ("reminder_logs", "member_id"),
    ("query_logs", "member_id"),
    ("admin_audit_logs", "member_id"),
    ("team_settings", "updated_by_member_id"),
    ("ai_accounts", "primary_member_id"),
    ("ai_account_credentials", "assignee_member_id"),
    ("ai_account_credentials", "bound_by_member_id"),
    ("ai_account_plan_history", "changed_by_member_id"),
    ("usage_summaries", "submitted_by_member_id"),
    ("key_loans", "borrower_member_id"),
    ("access_requests", "applicant_member_id"),
    ("access_requests", "manager_member_id"),
    ("access_requests", "decided_by_member_id"),
    ("knowledge_entries", "author_member_id"),
    ("proxy_keys", "member_id"),
    ("capability_invocations", "actor_member_id"),
    ("portal_chat_deliveries", "member_id"),
)


class IdentityError(ValueError):
    pass


# Higher wins when merging portal roles onto the kept Member.
_PORTAL_ROLE_RANK: dict[str, int] = {
    "ai_member": 1,
    "auditor": 2,
    "operator": 3,
    "custom": 3,
    "owner": 4,
}


def _portal_role_rank(role: str | None) -> int:
    if not role:
        return 0
    return _PORTAL_ROLE_RANK.get(role, 0)


def validate_web_username(username: str) -> str:
    value = (username or "").strip()
    if not _USERNAME_RE.match(value):
        raise IdentityError("用户名需为 2–64 位字母、数字或 ._-")
    if value.lower() in _RESERVED_WEB_USERNAMES:
        raise IdentityError(f"用户名 {value.lower()} 为系统保留名")
    return value


def list_identities(session: Session, member_id: str) -> list[MemberIdentity]:
    return list(
        session.scalars(
            select(MemberIdentity)
            .where(MemberIdentity.member_id == member_id)
            .order_by(MemberIdentity.channel.asc(), MemberIdentity.external_id.asc())
        ).all()
    )


def external_id_for(session: Session, member: Member, channel: str) -> str | None:
    """Return the external id for a channel, preferring identity rows."""
    channel = (channel or "").strip().lower()
    for row in list_identities(session, member.id):
        if row.channel == channel:
            return row.external_id
    if (member.channel or "").strip().lower() == channel:
        value = (member.channel_user_id or "").strip()
        return value or None
    return None


def refresh_member_primary_cache(session: Session, member: Member) -> None:
    """Refresh denormalized Member.channel / channel_user_id (IM preferred)."""
    identities = list_identities(session, member.id)
    if not identities:
        return
    by_channel = {row.channel: row for row in identities}
    for channel in _PRIMARY_CHANNEL_ORDER:
        row = by_channel.get(channel)
        if row is not None:
            member.channel = row.channel
            member.channel_user_id = row.external_id
            return
    member.channel = identities[0].channel
    member.channel_user_id = identities[0].external_id


def resolve_member(
    session: Session,
    team_id: str,
    *,
    channel: str,
    external_id: str,
    heal_identity: bool = False,
) -> Member | None:
    external_id = (external_id or "").strip()
    channel = (channel or "").strip().lower()
    if not external_id or not channel:
        return None
    identity = session.scalar(
        select(MemberIdentity).where(
            MemberIdentity.team_id == team_id,
            MemberIdentity.channel == channel,
            MemberIdentity.external_id == external_id,
        )
    )
    if identity is not None:
        return session.get(Member, identity.member_id)

    # Legacy fallback: Member row still holds the only identity.
    member = session.scalar(
        select(Member).where(
            Member.team_id == team_id,
            Member.channel == channel,
            Member.channel_user_id == external_id,
        )
    )
    if member is not None and heal_identity:
        ensure_identity(session, member, channel=channel, external_id=external_id)
    return member


def ensure_identity(
    session: Session,
    member: Member,
    *,
    channel: str,
    external_id: str,
) -> MemberIdentity:
    channel = (channel or "").strip().lower()
    external_id = (external_id or "").strip()
    if not member.team_id:
        raise IdentityError("成员缺少 team_id")
    existing = session.scalar(
        select(MemberIdentity).where(
            MemberIdentity.team_id == member.team_id,
            MemberIdentity.channel == channel,
            MemberIdentity.external_id == external_id,
        )
    )
    if existing is not None:
        if existing.member_id != member.id:
            raise IdentityError(
                f"身份 {channel}:{external_id} 已关联到其他成员，请先合并"
            )
        return existing
    row = MemberIdentity(
        team_id=member.team_id,
        member_id=member.id,
        channel=channel,
        external_id=external_id,
    )
    session.add(row)
    session.flush()
    refresh_member_primary_cache(session, member)
    return row


def link_identity(
    session: Session,
    member: Member,
    *,
    channel: str,
    external_id: str,
    merge_if_taken: bool = False,
    actor: Member | None = None,
) -> Member:
    """Attach an identity to member; optionally merge the current owner."""
    channel = (channel or "").strip().lower()
    external_id = (external_id or "").strip()
    if channel not in ("web", "dingtalk", "feishu"):
        raise IdentityError(f"不支持的渠道: {channel}")
    if not external_id:
        raise IdentityError("external_id 不能为空")
    if channel == "web":
        external_id = validate_web_username(external_id)

    existing = session.scalar(
        select(MemberIdentity).where(
            MemberIdentity.team_id == member.team_id,
            MemberIdentity.channel == channel,
            MemberIdentity.external_id == external_id,
        )
    )
    if existing is None:
        # Also check legacy Member row without identity yet.
        other = session.scalar(
            select(Member).where(
                Member.team_id == member.team_id,
                Member.channel == channel,
                Member.channel_user_id == external_id,
                Member.id != member.id,
            )
        )
        if other is not None:
            ensure_identity(session, other, channel=channel, external_id=external_id)
            if not merge_if_taken:
                raise IdentityError(
                    f"身份已被 {other.display_name} 占用；确认合并请传 merge=true"
                )
            return merge_members(session, keep=member, drop=other, actor=actor)
        ensure_identity(session, member, channel=channel, external_id=external_id)
        return member

    if existing.member_id == member.id:
        return member
    other = session.get(Member, existing.member_id)
    if other is None:
        session.delete(existing)
        session.flush()
        ensure_identity(session, member, channel=channel, external_id=external_id)
        return member
    if not merge_if_taken:
        raise IdentityError(
            f"身份已被 {other.display_name} 占用；确认合并请传 merge=true"
        )
    return merge_members(session, keep=member, drop=other, actor=actor)


def _meta_has_table(table: str) -> bool:
    # Use ORM metadata only — never inspect(engine) inside an open transaction
    # (SQLite pooled connections can roll back or block under reflection).
    return table in Base.metadata.tables


def _meta_has_column(table: str, column: str) -> bool:
    tbl = Base.metadata.tables.get(table)
    return tbl is not None and column in tbl.c


def _reassign_member_fks(session: Session, *, keep_id: str, drop_id: str) -> None:
    for table, column in _MEMBER_FK_UPDATES:
        if not _meta_has_table(table) or not _meta_has_column(table, column):
            continue
        session.execute(
            text(f"UPDATE {table} SET {column} = :keep WHERE {column} = :drop"),
            {"keep": keep_id, "drop": drop_id},
        )


def _merge_account_memberships(session: Session, *, keep_id: str, drop_id: str) -> None:
    if not _meta_has_table("ai_account_members"):
        return
    drop_rows = list(
        session.scalars(select(AiAccountMember).where(AiAccountMember.member_id == drop_id)).all()
    )
    for row in drop_rows:
        exists = session.get(AiAccountMember, {"account_id": row.account_id, "member_id": keep_id})
        if exists is not None:
            session.delete(row)
        else:
            row.member_id = keep_id
    session.flush()


def _assert_merge_ledger_ok(session: Session, keep: Member, drop: Member) -> None:
    """Block merge when both are primary on different accounts."""
    if not _meta_has_table("ai_accounts"):
        return
    keep_primary = list(
        session.scalars(select(AiAccount).where(AiAccount.primary_member_id == keep.id)).all()
    )
    drop_primary = list(
        session.scalars(select(AiAccount).where(AiAccount.primary_member_id == drop.id)).all()
    )
    if keep_primary and drop_primary:
        raise IdentityError(
            "双方都是不同台账的主使用人，请先调整台账负责人后再合并"
        )


def _count_active_owners(session: Session, team_id: str, *, exclude_id: str | None = None) -> int:
    stmt = select(Member).where(
        Member.team_id == team_id,
        Member.portal_role == "owner",
        Member.portal_status == "active",
    )
    if exclude_id:
        stmt = stmt.where(Member.id != exclude_id)
    return len(list(session.scalars(stmt).all()))


def merge_members(
    session: Session,
    *,
    keep: Member,
    drop: Member,
    actor: Member | None = None,
) -> Member:
    if keep.id == drop.id:
        return keep
    if keep.team_id != drop.team_id:
        raise IdentityError("不能跨团队合并成员")
    if actor is not None and drop.id == actor.id:
        raise IdentityError("不能合并删除当前登录账号")
    _assert_merge_ledger_ok(session, keep, drop)

    keep_id = keep.id
    drop_id = drop.id
    team_id = keep.team_id
    # Snapshot ORM fields before raw SQL / expire invalidates instances.
    drop_password = drop.password_hash
    drop_role = drop.portal_role
    drop_perms = drop.portal_permissions
    drop_portal_status = drop.portal_status
    drop_dept = drop.department_name
    drop_email = drop.cursor_email
    drop_status = drop.status
    keep_password = keep.password_hash
    keep_role = keep.portal_role
    keep_portal_status = keep.portal_status
    keep_dept = keep.department_name
    keep_email = keep.cursor_email
    keep_status = keep.status
    actor_role = actor.portal_role if actor is not None else None
    max_rank = _portal_role_rank(actor_role) if actor is not None else _portal_role_rank("owner")

    # Cap inherited role at the actor's own rank to prevent self-promotion via merge.
    inherited_role = drop_role
    inherited_perms = drop_perms
    if _portal_role_rank(drop_role) > max_rank:
        inherited_role = actor_role
        inherited_perms = actor.portal_permissions if actor is not None else None

    resulting_role = keep_role
    if _portal_role_rank(inherited_role) > _portal_role_rank(keep_role):
        resulting_role = inherited_role
    elif inherited_role and not keep_role:
        resulting_role = inherited_role
    if drop_role == "owner" and resulting_role != "owner":
        if _count_active_owners(session, team_id, exclude_id=drop_id) == 0:
            raise IdentityError("不能合并删除团队唯一的超级管理员")

    keep = session.get(Member, keep_id)
    drop = session.get(Member, drop_id)
    if keep is None or drop is None:
        raise IdentityError("合并成员不存在")

    for identity in list(list_identities(session, drop_id)):
        clash_id = session.scalar(
            select(MemberIdentity.id).where(
                MemberIdentity.team_id == team_id,
                MemberIdentity.channel == identity.channel,
                MemberIdentity.external_id == identity.external_id,
                MemberIdentity.member_id == keep_id,
            )
        )
        if clash_id is not None:
            session.execute(
                text("DELETE FROM member_identities WHERE id = :id"),
                {"id": identity.id},
            )
        else:
            session.execute(
                text("UPDATE member_identities SET member_id = :keep WHERE id = :id"),
                {"keep": keep_id, "id": identity.id},
            )
    # Drop ORM identity instances so later flushes cannot rewrite member_id.
    for obj in list(session.identity_map.values()):
        if isinstance(obj, MemberIdentity):
            session.expunge(obj)
    session.expire(keep, ["identities"])
    session.expire(drop, ["identities"])

    _merge_account_memberships(session, keep_id=keep_id, drop_id=drop_id)
    _reassign_member_fks(session, keep_id=keep_id, drop_id=drop_id)
    # Raw SQL updated FKs; expire ORM so a later flush cannot rewrite them.
    session.expire_all()
    keep = session.get(Member, keep_id)
    drop = session.get(Member, drop_id)
    if keep is None or drop is None:
        raise IdentityError("合并成员在重写外键后丢失")

    session.expunge(drop)
    session.execute(text("DELETE FROM members WHERE id = :id"), {"id": drop_id})
    session.flush()

    keep = session.get(Member, keep_id)
    if keep is None:
        raise IdentityError("合并后保留成员丢失")
    if drop_password and not keep_password:
        keep.password_hash = drop_password
    # Prefer higher portal role, but never above the acting admin's rank.
    if _portal_role_rank(inherited_role) > _portal_role_rank(keep_role):
        keep.portal_role = inherited_role
        keep.portal_permissions = inherited_perms
        keep.portal_status = drop_portal_status or keep_portal_status or "active"
    elif inherited_role and not keep_role:
        keep.portal_role = inherited_role
        keep.portal_permissions = inherited_perms
        keep.portal_status = drop_portal_status or "active"
    elif keep_portal_status != "active" and drop_portal_status == "active":
        keep.portal_status = "active"
        if inherited_role and not keep_role:
            keep.portal_role = inherited_role
            keep.portal_permissions = inherited_perms
    if drop_dept and not keep_dept:
        keep.department_name = drop_dept
    if drop_email and not keep_email:
        keep.cursor_email = drop_email
    if keep_status != "active" and drop_status == "active":
        keep.status = "active"
    session.flush()
    refresh_member_primary_cache(session, keep)
    return keep


def set_member_password(session: Session, member: Member, password: str) -> None:
    from pulse.web.passwords import hash_password

    if not password or len(password) < 6:
        raise IdentityError("密码至少 6 位")
    member.password_hash = hash_password(password)
    session.flush()


def identities_payload(session: Session, member: Member) -> list[dict]:
    return [
        {"channel": row.channel, "external_id": row.external_id}
        for row in list_identities(session, member.id)
    ]


def backfill_identities_for_team(session: Session, team_id: str) -> int:
    """Ensure every Member has at least one identity row (from cached columns)."""
    created = 0
    members = list(session.scalars(select(Member).where(Member.team_id == team_id)).all())
    for member in members:
        if not member.channel_user_id:
            continue
        before = session.scalar(
            select(MemberIdentity).where(
                MemberIdentity.team_id == team_id,
                MemberIdentity.channel == (member.channel or "web"),
                MemberIdentity.external_id == member.channel_user_id,
            )
        )
        if before is None:
            ensure_identity(
                session,
                member,
                channel=member.channel or "web",
                external_id=member.channel_user_id,
            )
            created += 1
    return created
