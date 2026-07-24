"""HMAC-signed actor claim headers for Pulse → Assistant BFF calls."""

from __future__ import annotations

import hashlib
import hmac
import time

ACTOR_CLAIM_VERSION = "v1"


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


def sign_actor_headers(
    token: str,
    member_id: str,
    role: str,
    channel_user_id: str,
    permissions: str,
    *,
    ts: int | None = None,
) -> dict[str, str]:
    ts_val = int(time.time()) if ts is None else ts
    payload = _actor_payload(member_id, role, channel_user_id, permissions, ts_val)
    signature = hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "X-Pulse-Actor-Member-Id": member_id,
        "X-Pulse-Actor-Role": role,
        "X-Pulse-Actor-Channel-User-Id": channel_user_id,
        "X-Pulse-Actor-Permissions": permissions,
        "X-Pulse-Actor-Ts": str(ts_val),
        "X-Pulse-Actor-Signature": signature,
    }
