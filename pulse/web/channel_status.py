"""Shared helpers for IM channel status in admin APIs."""

from __future__ import annotations

from typing import Any

from pulse.channels.base import normalize_platform


def resolve_im_group_status(effective: dict[str, Any]) -> dict[str, Any]:
    """Derive bot platform and whether the active channel's work group is set."""
    dingtalk = effective.get("dingtalk") or {}
    feishu = effective.get("feishu") or {}
    bot_platform = normalize_platform((effective.get("bot") or {}).get("name"))
    dingtalk_group = bool(dingtalk.get("group_open_conversation_id"))
    feishu_group = bool(feishu.get("group_chat_id"))
    if bot_platform == "feishu":
        im_group_configured = feishu_group
    elif bot_platform == "dingtalk":
        im_group_configured = dingtalk_group
    else:
        im_group_configured = False
    return {
        "bot_platform": bot_platform,
        "im_group_configured": im_group_configured,
        "dingtalk_group_configured": dingtalk_group,
        "feishu_group_configured": feishu_group,
    }
