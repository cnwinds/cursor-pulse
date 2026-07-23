from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from assistant_platform.config import AssistantConfig

logger = logging.getLogger(__name__)


def send_channel_reply(payload: dict[str, Any], config: AssistantConfig) -> dict[str, Any]:
    """POST reply payload to Pulse internal channel API."""
    if not config.pulse_internal_token:
        logger.info("reply.send skipped: PULSE_INTERNAL_TOKEN not configured")
        return {"status": "skipped", "reason": "no_internal_token"}

    import httpx

    url = f"{config.pulse_base_url.rstrip('/')}/api/internal/v1/channel/reply"
    kind = payload.get("kind", "final")
    session_id = payload.get("session_id", "")
    message_id = payload.get("message_id", "")
    logger.info(
        "reply.timing stage=reply_send_http_start session_id=%s message_id=%s kind=%s at=%s",
        session_id,
        message_id,
        kind,
        datetime.now(timezone.utc).isoformat(),
    )
    http_t0 = time.monotonic()
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {config.pulse_internal_token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        status = body.get("status") if isinstance(body, dict) else "unknown"
        logger.info(
            "reply.timing stage=reply_send_http_done session_id=%s message_id=%s kind=%s "
            "elapsed_ms=%d status=%s",
            session_id,
            message_id,
            kind,
            int((time.monotonic() - http_t0) * 1000),
            status,
        )
        if isinstance(body, dict) and body.get("status") != "sent":
            logger.warning(
                "reply.send accepted but not delivered: status=%s reason=%s payload=%s",
                body.get("status"),
                body.get("reason"),
                payload.get("reply_endpoint"),
            )
        return body if isinstance(body, dict) else {"status": status}
    except Exception:
        logger.exception("reply.send HTTP call failed")
        return {"status": "failed"}
