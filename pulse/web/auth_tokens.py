from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from pulse.storage.models import Member
from pulse.web.permissions import resolve_permissions


def _secret(config) -> str:
    secret = config.web.jwt_secret or config.web.admin_token
    if not secret:
        raise RuntimeError("未配置 JWT_SECRET 或 ADMIN_WEB_TOKEN")
    return secret


def create_access_token(config, member: Member, *, hours: int = 2) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": member.id,
        "dingtalk_user_id": member.dingtalk_user_id,
        "display_name": member.display_name,
        "portal_role": member.portal_role,
        "permissions": sorted(resolve_permissions(member)),
        "iat": now,
        "exp": now + timedelta(hours=hours),
        "type": "access",
    }
    return jwt.encode(payload, _secret(config), algorithm="HS256")


def decode_access_token(config, token: str) -> dict[str, Any]:
    return jwt.decode(token, _secret(config), algorithms=["HS256"])
