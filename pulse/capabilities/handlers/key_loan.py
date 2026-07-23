"""Capability handlers for key loan (structured arguments)."""

from __future__ import annotations

from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult

from pulse.capabilities.handlers.common import (
    _fail,
    _success,
    is_channel_admin,
    repository_for,
    resolve_actor_member,
)
from pulse.tool_center.key_loan_ops import (
    list_active_loans,
    request_loan_payload,
    return_loan,
    revoke_loan,
)


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_loan_note(arguments: dict[str, Any]) -> str | None:
    note = _optional_str(arguments, "note")
    if note is not None:
        return note
    text = _optional_str(arguments, "text")
    if text and " " in text:
        return text.split(maxsplit=1)[1].strip() or None
    return None


def _parse_loan_id_prefix(arguments: dict[str, Any]) -> str | None:
    prefix = _optional_str(arguments, "loan_id_prefix") or _optional_str(
        arguments, "loan_id"
    )
    if prefix:
        return prefix
    text = _optional_str(arguments, "text")
    if text and text.startswith("撤销借用 "):
        return text.split(maxsplit=1)[1].strip()
    return None


def handle_key_loan_request(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    note = _parse_loan_note(request.arguments)
    payload = request_loan_payload(repo, config, member, note=note)
    if not payload.get("ok"):
        return _fail(
            str(payload.get("error_code") or "loan_failed"),
            str(payload.get("error") or "借 Key 失败"),
        )
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "capability_key": "key.loan.request",
            "loan_id": payload.get("loan_id"),
            "lender_name": payload.get("lender_name"),
            "source_identifier": payload.get("source_identifier"),
            "api_key": payload.get("api_key"),
            "delivery_mode": payload.get("delivery_mode"),
            "warning": payload.get("warning"),
            "loan_expires_on": payload.get("loan_expires_on"),
        },
    )

def handle_key_loan_return(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    reply = return_loan(repo, config, member)
    if reply.startswith("归还失败"):
        return _fail("return_failed", reply)
    return _success(reply, capability_key="key.loan.return")


def handle_key_loan_self_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    from pulse.tool_center.key_loan_ops import build_self_loan_payload

    payload = build_self_loan_payload(repo, config, member)
    # Agent 按 Skill 排版；不再提供 user_message 文案。
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result=payload,
    )


def handle_key_loan_list(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    reply = list_active_loans(repo, config, team_id=request.team_id)
    return _success(reply, capability_key="key.loan.list")


def handle_key_loan_revoke(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    prefix = _parse_loan_id_prefix(request.arguments)
    if not prefix:
        return _fail("invalid_arguments", "缺少 loan_id_prefix")
    reply = revoke_loan(
        repo, config, loan_id_prefix=prefix, team_id=request.team_id
    )
    if reply.startswith("撤销失败"):
        return _fail("revoke_failed", reply)
    return _success(reply, capability_key="key.loan.revoke")
