from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.capabilities.invoke import invoke_capability
from pulse.config import AppConfig

logger = logging.getLogger(__name__)


def format_capability_reply(result: CapabilityInvokeResult) -> str:
    """Channel-facing text: prefer user_message, else result.text / result.answer / quota format."""
    if (result.user_message or "").strip():
        return result.user_message
    data = result.result if isinstance(result.result, dict) else {}
    for key in ("text", "answer", "message"):
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val)
    if data.get("empty_reason") == "no_cursor_account":
        return "尚未绑定 Cursor 账号"
    accounts = data.get("accounts")
    if isinstance(accounts, list) and accounts:
        from pulse.capabilities.handlers.quota_self_read import _format_user_message

        return _format_user_message(accounts)
    return ""


def invoke_via_assistant(
    *,
    config: AppConfig,
    team_id: str,
    member_id: str,
    role: str | None,
    capability_key: str,
    arguments: dict[str, Any],
    confirmed: bool = True,
    capability_version: str = "1",
) -> str:
    mirror = config.assistant_mirror
    url = f"{mirror.base_url.rstrip('/')}/api/assistant/v1/capabilities/invoke"
    headers = {"Content-Type": "application/json"}
    if mirror.service_token:
        headers["Authorization"] = f"Bearer {mirror.service_token}"
    payload = {
        "team_id": team_id,
        "actor_member_id": member_id,
        "role": role,
        "capability_key": capability_key,
        "capability_version": capability_version,
        "arguments": arguments,
        "confirmed": confirmed,
    }
    with httpx.Client(timeout=mirror.timeout_seconds) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    msg = str(data.get("user_message") or "").strip()
    if msg:
        return msg
    nested = data.get("result") if isinstance(data.get("result"), dict) else {}
    for key in ("text", "answer", "message"):
        val = nested.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return ""


def invoke_capability_local(
    session: Any,
    *,
    config: AppConfig,
    team_id: str,
    member_id: str,
    capability_key: str,
    arguments: dict[str, Any],
    confirmed: bool = True,
    capability_version: str = "1",
) -> str:
    request = CapabilityInvokeRequest(
        invocation_id=str(uuid.uuid4()),
        idempotency_key=str(uuid.uuid4()),
        team_id=team_id,
        actor_member_id=member_id,
        capability_key=capability_key,
        capability_version=capability_version,
        arguments=arguments,
        confirmed_by=member_id if confirmed else None,
    )
    result = invoke_capability(session, request=request, config=config)
    return format_capability_reply(result)
