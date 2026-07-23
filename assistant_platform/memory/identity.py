"""Pulse ↔ assistant identity helpers for semantic memory scope."""

from __future__ import annotations

from assistant_platform.memory.semantic.domain import VisibilityContext, team_id_to_namespace


def member_id_to_subject(member_id: str) -> str:
    return member_id


def dingtalk_context(*, is_group: bool, user_id: str) -> VisibilityContext:
    if is_group:
        return VisibilityContext.public()
    return VisibilityContext.private(user_id)


__all__ = [
    "VisibilityContext",
    "dingtalk_context",
    "member_id_to_subject",
    "team_id_to_namespace",
]
