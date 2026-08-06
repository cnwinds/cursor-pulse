"""Pulse client: record outbound IM into Assistant conversation ledger."""

from __future__ import annotations

import logging
from typing import Any

from pulse.channels.base import messenger_delivered, normalize_platform
from pulse.http_clients import internal_client

logger = logging.getLogger(__name__)


def _mirror_ready(config: Any) -> bool:
    mirror = getattr(config, "assistant_mirror", None)
    if mirror is None:
        return False
    if not getattr(mirror, "enabled", False):
        return False
    if not (getattr(mirror, "base_url", None) or "").strip():
        return False
    if not (getattr(mirror, "service_token", None) or "").strip():
        return False
    return True


def resolve_outbound_channel(config: Any) -> str | None:
    platform = normalize_platform(getattr(getattr(config, "bot", None), "name", None))
    if platform in ("dingtalk", "feishu"):
        return platform
    return None


def resolve_group_conversation_id(config: Any, channel: str | None = None) -> str | None:
    ch = channel or resolve_outbound_channel(config)
    if ch == "dingtalk":
        return (getattr(getattr(config, "dingtalk", None), "group_open_conversation_id", None) or "").strip() or None
    if ch == "feishu":
        return (getattr(getattr(config, "feishu", None), "group_chat_id", None) or "").strip() or None
    return None


def resolve_team_id(config: Any, session=None) -> str | None:
    """Resolve default tenant team id; opens a short-lived DB session when needed."""
    if session is not None:
        from pulse.tenant.context import team_repository

        team, _ = team_repository(session, config)
        return team.id

    database_url = getattr(getattr(config, "storage", None), "database_url", None)
    if not database_url:
        return None
    from pulse.storage.db import init_db
    from pulse.tenant.context import team_repository

    sf = init_db(database_url)
    db = sf()
    try:
        team, _ = team_repository(db, config)
        return team.id
    except Exception:
        logger.exception("resolve_team_id failed")
        return None
    finally:
        db.close()


def record_outbound_ledger(
    config: Any,
    *,
    team_id: str,
    channel: str,
    conversation_type: str,
    text: str,
    source: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    kind: str = "notify",
) -> dict[str, Any] | None:
    """POST outbound message into Assistant ledger. Failures are logged only."""
    if not _mirror_ready(config):
        logger.debug(
            "outbound ledger skipped: assistant_mirror not ready source=%s",
            source,
        )
        return None

    mirror = config.assistant_mirror
    url = f"{mirror.base_url.rstrip('/')}/api/assistant/v1/ledger/outbound"
    payload = {
        "team_id": team_id,
        "channel": channel,
        "conversation_type": conversation_type,
        "text": text,
        "source": source,
        "kind": kind or "notify",
        "user_id": user_id,
        "conversation_id": conversation_id,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {mirror.service_token}",
    }
    timeout = max(float(getattr(mirror, "timeout_seconds", 2.0) or 2.0), 2.0)
    try:
        with internal_client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            return body if isinstance(body, dict) else {"status": "recorded"}
    except Exception:
        logger.exception(
            "outbound ledger record failed source=%s channel=%s type=%s",
            source,
            channel,
            conversation_type,
        )
        return None


def send_oto_and_ledger(
    config: Any,
    messenger: Any,
    *,
    user_id: str,
    text: str,
    source: str,
    team_id: str | None = None,
    channel: str | None = None,
    kind: str = "notify",
    session=None,
) -> dict | None:
    """Send private IM then record to ledger on successful delivery."""
    result = messenger.send_oto_text(user_id, text)
    if not messenger_delivered(result):
        return result
    if not _mirror_ready(config):
        return result

    ch = channel or resolve_outbound_channel(config)
    if not ch:
        return result
    tid = team_id or resolve_team_id(config, session=session)
    if not tid:
        logger.warning("outbound ledger skipped: no team_id source=%s", source)
        return result

    record_outbound_ledger(
        config,
        team_id=tid,
        channel=ch,
        conversation_type="private",
        user_id=user_id,
        text=text,
        source=source,
        kind=kind,
    )
    return result


def send_group_and_ledger(
    config: Any,
    messenger: Any,
    *,
    text: str,
    source: str,
    team_id: str | None = None,
    channel: str | None = None,
    conversation_id: str | None = None,
    kind: str = "notify",
    at_all: bool = False,
    session=None,
) -> dict | None:
    """Send group IM then record to ledger on successful delivery."""
    result = messenger.send_group_text(text, at_all=at_all)
    if not messenger_delivered(result):
        return result
    if not _mirror_ready(config):
        return result

    ch = channel or resolve_outbound_channel(config)
    if not ch:
        return result
    cid = conversation_id or resolve_group_conversation_id(config, ch)
    if not cid:
        logger.warning("outbound ledger skipped: no group conversation_id source=%s", source)
        return result
    tid = team_id or resolve_team_id(config, session=session)
    if not tid:
        logger.warning("outbound ledger skipped: no team_id source=%s", source)
        return result

    record_outbound_ledger(
        config,
        team_id=tid,
        channel=ch,
        conversation_type="group",
        conversation_id=cid,
        text=text,
        source=source,
        kind=kind,
    )
    return result
