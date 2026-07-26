"""Dedicated capability handlers (Phase C)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.capabilities.handlers.common import (
    _fail,
    _success,
    is_channel_admin,
    repository_for,
    resolve_actor_member,
)
from pulse.channels.commands import handle_unbind_cursor_command
from pulse.periods import current_period
from pulse.storage.models import UsageIngestion

logger = logging.getLogger(__name__)


def _text_arg(request: CapabilityInvokeRequest) -> str:
    return str(request.arguments.get("text") or "").strip()


def _period_arg(request: CapabilityInvokeRequest, config: Any) -> str:
    period = request.arguments.get("period")
    if isinstance(period, str) and period.strip():
        return period.strip()
    text = _text_arg(request)
    parts = text.split()
    if len(parts) > 1:
        return parts[1]
    return current_period(config)


def handle_bot_help(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    from assistant_platform.conversation.help import build_help_message_from_keys

    allowed = request.arguments.get("allowed_capability_keys")
    topic = request.arguments.get("topic")
    topic_str = str(topic) if topic else None
    if isinstance(allowed, list) and allowed:
        return _success(
            build_help_message_from_keys(
                allowed,
                topic=topic_str,
                member_id=request.actor_member_id,
            ),
            capability_key="bot.help",
        )
    from pulse.channels.commands import build_bot_help_message

    return _success(
        build_bot_help_message(topic=topic_str),
        capability_key="bot.help",
    )


def handle_submission_self_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    period = _period_arg(request, config)
    sub = session.scalar(
        select(UsageIngestion)
        .where(UsageIngestion.member_id == member.id, UsageIngestion.billing_period == period)
        .order_by(UsageIngestion.ingested_at.desc())
    )
    if not sub:
        return _success(f"{period} 暂无提交记录。", capability_key="submission.self.read")
    return _success(
        f"{period} 已于 {sub.ingested_at.isoformat()} 提交（{sub.channel}）。",
        capability_key="submission.self.read",
    )


def handle_submission_status_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    from pulse.tool_center.repository import ToolCenterRepository

    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.channel_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    period = _period_arg(request, config)
    tool_repo = ToolCenterRepository(session, request.team_id)
    active = tool_repo.list_active_accounts()
    submitted = tool_repo.get_submitted_account_ids(period)
    lines = [f"📋 {period} 账号同步进度：{len(submitted)}/{len(active)}"]
    for account in active:
        mark = "✅" if account.id in submitted else "❌"
        lines.append(f"{mark} {account.account_identifier}")
    if not active:
        lines.append("（尚未配置 active 账号）")
    return _success("\n".join(lines), capability_key="submission.status.read")


def handle_cursor_key_unbind(
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
    text = _text_arg(request)
    if not text:
        email = request.arguments.get("email")
        if isinstance(email, str) and email.strip():
            text = f"解绑 cursor {email.strip()}"
        else:
            text = "解绑 cursor"
    reply = handle_unbind_cursor_command(
        text,
        member.channel_user_id,
        config,
        repo,
        display_name=member.display_name,
    )
    if reply is None:
        return _fail("invalid_arguments", "无法解析解绑命令")
    return _success(reply, capability_key="cursor.key.unbind")


def handle_members_manage(
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
    if not is_channel_admin(member.channel_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    text = _text_arg(request) or "成员"
    parts = text.split()
    if len(parts) == 1:
        active = repo.list_active_members()
        if not active:
            return _success("暂无 active 成员。使用「成员 添加 userid 姓名」添加。", capability_key="members.manage")
        lines = ["👥 成员名单（active）："]
        for m in active:
            lines.append(f"· {m.display_name} ({m.channel_user_id})")
        return _success("\n".join(lines), capability_key="members.manage")
    if parts[1] == "添加" and len(parts) >= 4:
        uid, name = parts[2], parts[3]
        repo.add_member(uid, name)
        return _success(f"已添加成员 {name}（{uid}）", capability_key="members.manage")
    if parts[1] == "移除" and len(parts) >= 3:
        uid = parts[2]
        target = repo.get_member_by_channel_user_id(uid)
        if not target:
            return _fail("not_found", f"未找到 {uid}")
        target.status = "inactive"
        return _success(f"已将 {target.display_name} 设为 inactive", capability_key="members.manage")
    return _fail("invalid_arguments", "用法：成员 | 成员 添加 userid 姓名 | 成员 移除 userid")
