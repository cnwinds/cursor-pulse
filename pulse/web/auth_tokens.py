from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pulse.storage.models import Member, PortalRefreshToken
from pulse.web.permissions import can_access_portal, resolve_permissions

logger = logging.getLogger(__name__)
_admin_token_fallback_warned = False


class RefreshTokenError(Exception):
    """Refresh token is invalid, expired, revoked, or reused."""


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


def _access_token_minutes(config, minutes: int | None) -> int:
    if minutes is not None:
        return minutes
    return int(getattr(config.web, "access_token_minutes", 30) or 30)


def _refresh_token_days(config) -> int:
    return int(getattr(config.web, "refresh_token_days", 7) or 7)


def create_access_token(
    config,
    member: Member,
    *,
    minutes: int | None = None,
    hours: int | None = None,
) -> str:
    """Issue a short-lived access JWT.

    ``minutes`` is preferred. ``hours`` is retained for older call sites/tests
    and converts to minutes when ``minutes`` is omitted.
    """
    if minutes is None and hours is not None:
        minutes = int(hours) * 60
    ttl = _access_token_minutes(config, minutes)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": member.id,
        "channel": getattr(member, "channel", None) or "web",
        "channel_user_id": member.channel_user_id,
        "display_name": member.display_name,
        "portal_role": member.portal_role,
        "permissions": sorted(resolve_permissions(member)),
        "iat": now,
        "exp": now + timedelta(minutes=ttl),
        "type": "access",
    }
    return jwt.encode(payload, _secret(config), algorithm="HS256")


def decode_access_token(config, token: str) -> dict[str, Any]:
    payload = jwt.decode(token, _secret(config), algorithms=["HS256"])
    if payload.get("type") not in (None, "access"):
        raise jwt.InvalidTokenError("not an access token")
    return payload


def create_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def issue_token_pair(session: Session, config, member: Member) -> dict[str, Any]:
    """Create access + refresh tokens; persist refresh hash on ``session`` (caller commits)."""
    access_minutes = _access_token_minutes(config, None)
    refresh_days = _refresh_token_days(config)
    now = datetime.now(timezone.utc)
    raw_refresh = create_refresh_token()
    row = PortalRefreshToken(
        member_id=member.id,
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=now + timedelta(days=refresh_days),
    )
    session.add(row)
    session.flush()
    return {
        "access_token": create_access_token(config, member, minutes=access_minutes),
        "refresh_token": raw_refresh,
        "expires_in": access_minutes * 60,
        "refresh_expires_at": row.expires_at,
        "refresh_row_id": row.id,
    }


def rotate_refresh_token(session: Session, config, raw: str) -> dict[str, Any]:
    """Validate refresh token, rotate it, and return a new token pair.

    Uses row lock + CAS revoke so concurrent refreshes cannot mint two live pairs.
    Reuse of an already-rotated token fails closed without revoking sibling sessions
    (avoids multi-tab false logout of the winner).
    """
    raw = (raw or "").strip()
    if not raw:
        raise RefreshTokenError("missing refresh token")
    now = datetime.now(timezone.utc)
    token_hash = hash_refresh_token(raw)
    row = session.scalar(
        select(PortalRefreshToken)
        .where(PortalRefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    if row is None:
        raise RefreshTokenError("invalid refresh token")

    if row.revoked_at is not None:
        # Already rotated/logged out. Do not revoke-all: concurrent tabs often race
        # on the same refresh and the winner must keep its new session.
        raise RefreshTokenError("refresh token reuse detected")

    if _ensure_aware(row.expires_at) <= now:
        row.revoked_at = now
        session.flush()
        raise RefreshTokenError("refresh token expired")

    member = session.get(Member, row.member_id)
    if member is None or not can_access_portal(member):
        row.revoked_at = now
        session.flush()
        raise RefreshTokenError("member not allowed")

    pair = issue_token_pair(session, config, member)
    result = session.execute(
        update(PortalRefreshToken)
        .where(
            PortalRefreshToken.id == row.id,
            PortalRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now, replaced_by_id=pair["refresh_row_id"])
    )
    if result.rowcount != 1:
        # Lost the CAS race — drop the orphan pair we just created.
        session.execute(
            update(PortalRefreshToken)
            .where(PortalRefreshToken.id == pair["refresh_row_id"])
            .values(revoked_at=now)
        )
        session.flush()
        raise RefreshTokenError("refresh token race")
    session.flush()
    return {
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "expires_in": pair["expires_in"],
        "member": member,
    }


def revoke_refresh_token(session: Session, raw: str) -> bool:
    """Revoke a refresh token if present. Returns True when a row was updated."""
    raw = (raw or "").strip()
    if not raw:
        return False
    now = datetime.now(timezone.utc)
    row = session.scalar(
        select(PortalRefreshToken).where(
            PortalRefreshToken.token_hash == hash_refresh_token(raw)
        )
    )
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = now
        session.flush()
    return True
