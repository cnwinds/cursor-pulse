"""Verified Pulse actor context from HMAC-signed claim headers."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated, Callable

from fastapi import Header
from pydantic import BaseModel, Field

ACTOR_CLAIM_VERSION = "v1"
MAX_ACTOR_TS_SKEW_SECONDS = 300


class ActorContext(BaseModel):
    member_id: str = ""
    role: str = ""
    channel_user_id: str = ""
    permissions: set[str] = Field(default_factory=set)


def _parse_permissions(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _actor_payload(
    member_id: str,
    role: str,
    channel_user_id: str,
    permissions: str,
    ts: int,
) -> bytes:
    return (
        f"{ACTOR_CLAIM_VERSION}\n"
        f"{member_id}\n"
        f"{role}\n"
        f"{channel_user_id}\n"
        f"{permissions}\n"
        f"{ts}"
    ).encode()


def verify_and_build_actor(
    service_token: str,
    *,
    member_id: str | None,
    role: str | None,
    channel_user_id: str | None,
    permissions: str | None,
    ts: str | None,
    signature: str | None,
) -> ActorContext:
    empty = ActorContext()
    token = (service_token or "").strip()
    if not token or not ts or not signature:
        return empty

    member_id_s = (member_id or "").strip()
    role_s = (role or "").strip()
    channel_user_id_s = (channel_user_id or "").strip()
    permissions_s = (permissions or "").strip()
    signature_s = signature.strip()

    try:
        ts_int = int(ts)
    except ValueError:
        return empty

    now = int(time.time())
    if abs(now - ts_int) > MAX_ACTOR_TS_SKEW_SECONDS:
        return empty

    payload = _actor_payload(member_id_s, role_s, channel_user_id_s, permissions_s, ts_int)
    expected = hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_s):
        return empty

    return ActorContext(
        member_id=member_id_s,
        role=role_s,
        channel_user_id=channel_user_id_s,
        permissions=_parse_permissions(permissions_s),
    )


def build_actor_dependency(service_token: str) -> Callable[..., ActorContext]:
    def dependency(
        x_pulse_actor_member_id: Annotated[str | None, Header(alias="X-Pulse-Actor-Member-Id")] = None,
        x_pulse_actor_role: Annotated[str | None, Header(alias="X-Pulse-Actor-Role")] = None,
        x_pulse_actor_channel_user_id: Annotated[
            str | None, Header(alias="X-Pulse-Actor-Channel-User-Id")
        ] = None,
        x_pulse_actor_permissions: Annotated[
            str | None, Header(alias="X-Pulse-Actor-Permissions")
        ] = None,
        x_pulse_actor_ts: Annotated[str | None, Header(alias="X-Pulse-Actor-Ts")] = None,
        x_pulse_actor_signature: Annotated[str | None, Header(alias="X-Pulse-Actor-Signature")] = None,
    ) -> ActorContext:
        return verify_and_build_actor(
            service_token,
            member_id=x_pulse_actor_member_id,
            role=x_pulse_actor_role,
            channel_user_id=x_pulse_actor_channel_user_id,
            permissions=x_pulse_actor_permissions,
            ts=x_pulse_actor_ts,
            signature=x_pulse_actor_signature,
        )

    return dependency
