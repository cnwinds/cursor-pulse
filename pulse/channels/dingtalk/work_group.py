"""钉钉工作群：群内 @ 发送「启动」绑定 openConversationId 并欢迎全员。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pulse.channels.admin_gate import is_dingtalk_admin
from pulse.channels.dingtalk.group_store import save_group_binding
from pulse.config import AppConfig
from pulse.web.audit import log_admin_action
from pulse.settings import patch_team_setting

logger = logging.getLogger(__name__)

_ACTIVATION_EXACT = frozenset(
    {
        "启动",
        "激活",
        "开始",
        "开工",
        "/start",
        "start",
        "activate",
    }
)


@dataclass(frozen=True)
class WorkGroupActivationResult:
    handled: bool
    reply: str | None = None
    binding_changed: bool = False


def is_work_group_activation(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    first_line = normalized.splitlines()[0].strip()
    lowered = first_line.lower()
    return first_line in _ACTIVATION_EXACT or lowered in {s.lower() for s in _ACTIVATION_EXACT}


def can_activate_work_group(
    config: AppConfig,
    *,
    user_id: str,
    member_portal_role: str,
    current_open_id: str,
    target_open_id: str,
) -> tuple[bool, str]:
    if not target_open_id:
        return False, "无法识别群 ID，请稍后重试。"
    if not current_open_id:
        return True, ""
    if current_open_id == target_open_id:
        return True, ""
    if member_portal_role in ("owner", "operator"):
        return True, ""
    if is_dingtalk_admin(user_id, config.admin.dingtalk_user_ids):
        return True, ""
    return False, "工作群已绑定到其他群，仅管理员可在新群发送「启动」重新绑定。"


def build_work_group_welcome_message(
    config: AppConfig,
    *,
    group_title: str | None = None,
) -> str:
    name = (config.persona.name or "小脉").strip() or "小脉"
    group_label = f"「{group_title}」" if group_title else "本群"
    return (
        f"大家好，我是 **{name}**，团队的 AI 工具用量助手。\n\n"
        f"{group_label} 已设为 **工作群**。后续月报、催办等群通知会发在这里。\n\n"
        "**在群里 @ 我你可以：**\n"
        "· 发送「帮助」查看你能用的命令\n"
        "· 分享心得（`心得：…`）\n\n"
        "**请私聊我处理这些事项（更安全）：**\n"
        "· 绑定 / 解绑 Cursor Key、查额度\n"
        "· 查我的提交、借还临时 Key\n"
        "· 查询个人用量\n\n"
        "随时私聊发送「帮助」，我会按你的权限列出可用功能。"
    )


def persist_work_group_binding(
    config: AppConfig,
    session: Any,
    *,
    team_id: str,
    open_conversation_id: str,
    chat_id: str | None = None,
    title: str | None = None,
    member_id: str | None = None,
) -> bool:
    previous = (config.dingtalk.group_open_conversation_id or "").strip()
    changed = previous != open_conversation_id

    config.dingtalk.group_open_conversation_id = open_conversation_id
    if chat_id:
        config.dingtalk.chat_id = chat_id
    if title:
        config.dingtalk.group_title = title

    save_group_binding(
        open_conversation_id=open_conversation_id,
        chat_id=chat_id or config.dingtalk.chat_id or None,
        title=title or config.dingtalk.group_title or None,
        team_slug=config.tenant.slug,
        database_url=config.storage.database_url,
        member_id=member_id,
        session=session,
        team_id=team_id,
    )

    if member_id:
        detail = open_conversation_id
        if title:
            detail = f"{title}:{open_conversation_id}"
        log_admin_action(
            session,
            team_id=team_id,
            member_id=member_id,
            action="dingtalk.work_group.bind",
            capability="settings:write",
            detail=detail,
        )

    logger.info(
        "工作群已绑定 openConversationId=%s title=%s changed=%s",
        open_conversation_id,
        title,
        changed,
    )
    return changed


def activate_work_group(
    config: AppConfig,
    session: Any,
    *,
    team_id: str,
    incoming: Any,
    user_id: str,
    member_id: str,
    member_portal_role: str,
) -> WorkGroupActivationResult:
    open_id = (getattr(incoming, "conversation_id", None) or "").strip()
    current = (config.dingtalk.group_open_conversation_id or "").strip()
    allowed, deny_reason = can_activate_work_group(
        config,
        user_id=user_id,
        member_portal_role=member_portal_role,
        current_open_id=current,
        target_open_id=open_id,
    )
    if not allowed:
        return WorkGroupActivationResult(handled=True, reply=deny_reason)

    title = getattr(incoming, "conversation_title", None) or None
    chat_id = (config.dingtalk.chat_id or "").strip() or None
    binding_changed = persist_work_group_binding(
        config,
        session,
        team_id=team_id,
        open_conversation_id=open_id,
        chat_id=chat_id,
        title=title,
        member_id=member_id,
    )

    welcome = build_work_group_welcome_message(config, group_title=title)
    if binding_changed and current:
        welcome = (
            f"工作群已切换至「{title or '当前群'}」。\n\n{welcome}"
            if title
            else f"工作群已切换。\n\n{welcome}"
        )
    elif not binding_changed and current:
        welcome = f"本群已是工作群。\n\n{welcome}"

    return WorkGroupActivationResult(
        handled=True,
        reply=welcome,
        binding_changed=binding_changed,
    )


def sync_group_display_name(
    config: AppConfig,
    session: Any,
    *,
    team_id: str,
    title: str | None,
    member_id: str | None = None,
) -> None:
    """群消息里带回的 conversation_title 写入团队设置，供后台展示。"""
    normalized = (title or "").strip()
    if not normalized or config.dingtalk.group_title == normalized:
        return
    config.dingtalk.group_title = normalized
    patch_team_setting(
        session,
        team_id=team_id,
        section="dingtalk",
        patch={"group_title": normalized},
        member_id=member_id,
    )
