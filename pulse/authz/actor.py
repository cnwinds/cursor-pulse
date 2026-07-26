from __future__ import annotations

from typing import Any


def is_channel_admin(member: Any, config: Any) -> bool:
    """Channel 侧管理员：owner/operator 或配置的钉钉 admin userid。"""
    if getattr(member, "portal_role", None) in ("owner", "operator"):
        return True
    from pulse.channels.admin_gate import is_channel_admin

    candidates: set[str] = set()
    cached = (getattr(member, "channel_user_id", None) or "").strip()
    if cached:
        candidates.add(cached)
    try:
        from sqlalchemy.orm import object_session

        from pulse.identity.service import list_identities

        session = object_session(member)
        if session is not None and getattr(member, "id", None):
            for row in list_identities(session, member.id):
                if row.external_id:
                    candidates.add(row.external_id)
    except Exception:
        pass
    return any(is_channel_admin(uid, config.admin.channel_user_ids) for uid in candidates)


def can_manage_guide_image(member: Any, config: Any) -> bool:
    return is_channel_admin(member, config)
