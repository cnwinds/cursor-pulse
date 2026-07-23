from __future__ import annotations

from pulse.storage.models import Member

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "settings:read",
        "settings:write",
        "submissions:read",
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
        "knowledge:read",
        "knowledge:write",
        "proxy:read",
        "proxy:write",
        "assistant:capabilities:read",
        "assistant:capabilities:write",
        "assistant:skills:read",
        "assistant:sessions:read:self",
        "assistant:sessions:read:all",
        "assistant:sessions:export:self",
        "assistant:sessions:export:all",
        "assistant:sessions:delete:self",
        "assistant:prompts:read",
        "assistant:prompts:write",
        "assistant:prompts:approve",
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "operator": frozenset(
        {
            "settings:read",
            "submissions:read",
            "metrics:read",
            "metrics:aggregate",
            "tasks:nudge",
            "audit:read",
            "accounts:read",
            "accounts:write",
            "knowledge:read",
            "knowledge:write",
            "proxy:read",
            "proxy:write",
            "assistant:capabilities:read",
            "assistant:capabilities:write",
            "assistant:skills:read",
            "assistant:sessions:read:self",
            "assistant:sessions:read:all",
            "assistant:sessions:export:self",
            "assistant:sessions:export:all",
            "assistant:sessions:delete:self",
            "assistant:prompts:read",
            "assistant:prompts:write",
        }
    ),
    "auditor": frozenset(
        {
            "settings:read",
            "submissions:read",
            "metrics:read",
            "audit:read",
            "accounts:read",
            "knowledge:read",
            "proxy:read",
        }
    ),
    "ai_member": frozenset(
        {
            "knowledge:read",
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
    "operator": "指标聚合、催办与账号管理",
    "auditor": "只读访问指标、审计日志与业务只读数据",
    "ai_member": "浏览技巧知识库、查看本人账号提交进度",
    "custom": "按需勾选能力码",
}


def can_access_portal(member: Member) -> bool:
    return (
        member.portal_status == "active"
        and member.status == "active"
        and bool(member.portal_role)
    )


def has_permission(member: Member, capability: str) -> bool:
    if capability in {
        "assistant:prompts:write",
        "assistant:prompts:approve",
    }:
        return False
    return capability in resolve_permissions(member)
