from __future__ import annotations

from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.capabilities.handlers.common import resolve_actor_member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.usage_self import build_usage_self_payload


def _encryption_key(config: Any) -> str:
    creds = getattr(config, "credentials", None)
    if creds is None:
        return ""
    return (getattr(creds, "encryption_key", None) or "").strip()


def handle_usage_self_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="成员不存在或无权访问",
        )

    text = str(request.arguments.get("text") or "我的用量").strip()
    tool_repo = ToolCenterRepository(session, request.team_id)
    accounts = tool_repo.get_primary_accounts_for_member(member.id)
    payload = build_usage_self_payload(
        session,
        accounts=accounts,
        text=text,
        config=config,
        member_id=member.id,
        team_id=request.team_id,
        encryption_key=_encryption_key(config),
    )
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result=payload,
    )
