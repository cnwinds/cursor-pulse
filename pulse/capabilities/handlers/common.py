"""Shared helpers for Pulse capability handlers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.channels.admin_gate import is_dingtalk_admin
from pulse.storage.models import Member
from pulse.storage.repository import Repository


def _success(reply: str, *, capability_key: str, **result_extra) -> CapabilityInvokeResult:
    """Succeeded invoke: never put display prose in user_message (Agent formats from result)."""
    result: dict[str, Any] = {
        "schema_version": 1,
        "capability_key": capability_key,
        "text": reply,
    }
    result.update(result_extra)
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result=result,
    )


def _fail(code: str, message: str) -> CapabilityInvokeResult:
    return CapabilityInvokeResult(
        status="failed",
        error_code=code,
        user_message=message,
    )


def resolve_actor_member(session, request: CapabilityInvokeRequest) -> Member | None:
    member = session.get(Member, request.actor_member_id)
    if member is not None and member.team_id == request.team_id:
        return member
    return session.scalar(
        select(Member).where(
            Member.team_id == request.team_id,
            Member.dingtalk_user_id == request.actor_member_id,
        )
    )


def repository_for(session, team_id: str) -> Repository:
    return Repository(session, team_id=team_id)


def is_channel_admin(user_id: str, config: Any, repo: Repository) -> bool:
    member = repo.get_member_by_dingtalk_id(user_id)
    if member and member.portal_role in ("owner", "operator"):
        return True
    admin_ids = set(getattr(getattr(config, "admin", None), "dingtalk_user_ids", None) or [])
    return is_dingtalk_admin(user_id, admin_ids)
