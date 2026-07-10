from __future__ import annotations

from pulse.storage.models import Member

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "settings:read",
        "settings:write",
        "submissions:read",
        "submissions:review",
        "metrics:read",
        "metrics:aggregate",
        "reports:publish",
        "memory:read",
        "memory:write",
        "evolution:run",
        "tasks:nudge",
        "tasks:group_message",
        "audit:read",
        "admin:users",
        "accounts:read",
        "accounts:write",
        "requests:read",
        "requests:write",
        "requests:approve",
        "knowledge:read",
        "knowledge:write",
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "operator": frozenset(
        {
            "settings:read",
            "submissions:read",
            "submissions:review",
            "metrics:read",
            "metrics:aggregate",
            "tasks:nudge",
            "audit:read",
            "accounts:read",
            "accounts:write",
            "requests:read",
            "requests:write",
            "requests:approve",
            "knowledge:read",
            "knowledge:write",
        }
    ),
    "auditor": frozenset(
        {
            "settings:read",
            "submissions:read",
            "metrics:read",
            "memory:read",
            "audit:read",
            "accounts:read",
            "requests:read",
            "knowledge:read",
        }
    ),
    "ai_member": frozenset(
        {
            "knowledge:read",
            "requests:read",
            "requests:write",
            "submissions:read",
        }
    ),
}


def resolve_permissions(member: Member) -> set[str]:
    if not member.portal_role:
        return set()
    if member.portal_role == "custom":
        raw = member.portal_permissions or []
        return {p for p in raw if p in ALL_PERMISSIONS}
    return set(ROLE_PERMISSIONS.get(member.portal_role, frozenset()))


PORTAL_ROLE_LABELS: dict[str, str] = {
    "owner": "超级管理员",
    "operator": "运营员",
    "auditor": "审计员",
    "ai_member": "AI工具成员",
    "custom": "自定义",
}

PORTAL_ROLE_DESCRIPTIONS: dict[str, str] = {
    "owner": "拥有全部权限，包括用户管理与审批",
    "operator": "提交审核、指标聚合与催办",
    "auditor": "只读访问指标、记忆与审计日志",
    "ai_member": "申请 AI 工具、浏览技巧知识库、查看本人账号提交进度",
    "custom": "按需勾选能力码",
}


def can_access_portal(member: Member) -> bool:
    return (
        member.portal_status == "active"
        and member.status == "active"
        and bool(member.portal_role)
    )


def has_permission(member: Member, capability: str) -> bool:
    return capability in resolve_permissions(member)
