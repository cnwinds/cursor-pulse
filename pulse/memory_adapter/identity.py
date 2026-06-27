from __future__ import annotations

from personamem import VisibilityContext


def member_id_to_subject(member_id: str) -> str:
    return member_id


def team_id_to_namespace(team_id: str) -> str:
    return f"team:{team_id}"


def dingtalk_context(*, is_group: bool, user_id: str) -> VisibilityContext:
    if is_group:
        return VisibilityContext.public()
    return VisibilityContext.private(user_id)
