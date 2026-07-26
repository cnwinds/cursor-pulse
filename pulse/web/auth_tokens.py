from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from pulse.storage.models import Member
from pulse.web.permissions import resolve_permissions

logger = logging.getLogger(__name__)
_admin_token_fallback_warned = False


def _is_production() -> bool:
    return os.environ.get("PULSE_ENV") == "production"


def _warn_admin_token_fallback() -> None:
    global _admin_token_fallback_warned
    if _admin_token_fallback_warned:
        return
    _admin_token_fallback_warned = True
    logger.warning(
        "JWT_SECRET not set; using ADMIN_WEB_TOKEN as JWT signing secret. "
        "Set JWT_SECRET explicitly (required when PULSE_ENV=production)."
    )


def assert_jwt_secret_configured(config) -> None:
    """Reject missing/short JWT_SECRET in production; warn once when falling back in dev."""
    jwt_secret = (config.web.jwt_secret or "").strip()
    if _is_production() and not jwt_secret:
        raise ValueError(
            "JWT_SECRET is required when PULSE_ENV=production "
            "(admin_token cannot substitute for JWT signing)."
        )
    if _is_production() and jwt_secret and len(jwt_secret.encode("utf-8")) < 32:
        raise ValueError(
            "JWT_SECRET must be at least 32 bytes when PULSE_ENV=production "
            "(RFC 7518 §3.2 recommendation for HS256)."
        )
    if not jwt_secret and (config.web.admin_token or "").strip():
        _warn_admin_token_fallback()
    elif jwt_secret and len(jwt_secret.encode("utf-8")) < 32:
        logger.warning(
            "JWT_SECRET is shorter than 32 bytes; use a longer secret in production."
        )


def _secret(config) -> str:
    jwt_secret = (config.web.jwt_secret or "").strip()
    if jwt_secret:
        return jwt_secret
    if _is_production():
        raise RuntimeError(
            "JWT_SECRET is required when PULSE_ENV=production "
            "(admin_token cannot substitute for JWT signing)."
        )
    admin_token = (config.web.admin_token or "").strip()
    if admin_token:
        _warn_admin_token_fallback()
        return admin_token
    raise RuntimeError("未配置 JWT_SECRET 或 ADMIN_WEB_TOKEN")


def create_access_token(config, member: Member, *, hours: int = 2) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": member.id,
        "channel": getattr(member, "channel", None) or "web",
        "channel_user_id": member.channel_user_id,
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
