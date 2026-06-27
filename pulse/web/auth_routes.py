from __future__ import annotations

from datetime import datetime, timezone

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.web.auth_tokens import create_access_token
from pulse.web.permissions import resolve_permissions


def member_payload(member: Member) -> dict:
    return {
        "id": member.id,
        "display_name": member.display_name,
        "dingtalk_user_id": member.dingtalk_user_id,
        "portal_role": member.portal_role,
        "permissions": sorted(resolve_permissions(member)),
        "last_portal_login_at": (
            member.last_portal_login_at.isoformat() if member.last_portal_login_at else None
        ),
    }


def auth_response(config: AppConfig, member: Member) -> dict:
    return {
        "access_token": create_access_token(config, member),
        "token_type": "bearer",
        "user": member_payload(member),
    }
