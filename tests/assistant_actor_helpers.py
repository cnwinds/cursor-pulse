"""Shared HMAC actor header helpers for Assistant API tests."""

from __future__ import annotations

import time

from pulse.web.assistant_actor import sign_actor_headers


def signed_actor_headers(
    token: str,
    *,
    member_id: str = "m1",
    role: str = "owner",
    channel_user_id: str = "",
    permissions: str = "assistant:sessions:read:all",
    ts: int | None = None,
) -> dict[str, str]:
    actor = sign_actor_headers(
        token,
        member_id,
        role,
        channel_user_id,
        permissions,
        ts=ts,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Assistant-Token": token,
        **actor,
    }
