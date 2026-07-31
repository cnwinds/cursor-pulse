from __future__ import annotations

from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.util.datetime_fmt import serialize_datetime
from pulse.web.auth_tokens import issue_token_pair
from pulse.web.permissions import resolve_permissions


def member_payload(member: Member) -> dict:
    return {
        "id": member.id,
        "display_name": member.display_name,
        "channel": getattr(member, "channel", None) or "web",
        "channel_user_id": member.channel_user_id,
        "portal_status": member.portal_status,
        "portal_role": member.portal_role,
        "permissions": sorted(resolve_permissions(member)),
        "last_portal_login_at": serialize_datetime(member.last_portal_login_at),
    }


def auth_response(config: AppConfig, member: Member, session: Session) -> dict:
    """Issue access + refresh pair. Caller must commit the session."""
    pair = issue_token_pair(session, config, member)
    return {
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "token_type": "bearer",
        "expires_in": pair["expires_in"],
        "user": member_payload(member),
    }
