from __future__ import annotations

from typing import Any


def is_channel_admin(member: Any, config: Any) -> bool:
    """Channel 侧管理员：owner/operator 或配置的钉钉 admin userid。"""
    if getattr(member, "portal_role", None) in ("owner", "operator"):
        return True
    from pulse.channels.admin_gate import is_dingtalk_admin

    return is_dingtalk_admin(member.dingtalk_user_id, config.admin.dingtalk_user_ids)


def can_manage_guide_image(member: Any, config: Any) -> bool:
    return is_channel_admin(member, config)
