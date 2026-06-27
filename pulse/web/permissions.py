from __future__ import annotations

from pulse.storage.models import Member

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "settings:read",
        "settings:write",
        "members:read",
        "members:write",
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
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "operator": frozenset(
        {
            "settings:read",
            "members:read",
            "members:write",
            "submissions:read",
            "submissions:review",
            "metrics:read",
            "metrics:aggregate",
            "tasks:nudge",
            "audit:read",
        }
    ),
    "auditor": frozenset(
        {
            "settings:read",
            "members:read",
            "submissions:read",
            "metrics:read",
            "memory:read",
            "audit:read",
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


def can_access_portal(member: Member) -> bool:
    return member.status == "active" and bool(member.portal_role)


def has_permission(member: Member, capability: str) -> bool:
    return capability in resolve_permissions(member)
