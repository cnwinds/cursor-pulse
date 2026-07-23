from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.config import AppConfig

logger = logging.getLogger(__name__)


class ChannelReplyBody(BaseModel):
    reply_endpoint: dict[str, Any] = Field(default_factory=dict)
    text: str
    session_id: str | None = None
    message_id: str | None = None
    kind: str = "final"


_recent_dingtalk_deliveries: dict[str, float] = {}
_dedupe_lock = threading.Lock()
_DEDUPE_TTL_SECONDS = 300.0


def _dedupe_key(*, message_id: str | None, text: str, kind: str) -> str | None:
    if message_id:
        return f"{message_id}:{kind}"
    return None


def _already_delivered(dedupe_key: str | None) -> bool:
    if not dedupe_key:
        return False
    import time

    now = time.monotonic()
    with _dedupe_lock:
        expired = [
            key
            for key, ts in _recent_dingtalk_deliveries.items()
            if now - ts > _DEDUPE_TTL_SECONDS
        ]
        for key in expired:
            _recent_dingtalk_deliveries.pop(key, None)
        if dedupe_key in _recent_dingtalk_deliveries:
            return True
        _recent_dingtalk_deliveries[dedupe_key] = now
        return False


def _get_dingtalk_messenger(config: AppConfig):
    if not config.dingtalk.app_key or not config.dingtalk.app_secret:
        return None
    try:
        from pulse.channels.dingtalk.messenger import DingTalkMessenger

        return DingTalkMessenger(config)
    except Exception:
        logger.exception("Failed to create DingTalk messenger for channel reply")
        return None


def _require_internal_service(config: AppConfig):
    def dependency(
        authorization: Annotated[str | None, Header()] = None,
        x_pulse_internal_token: Annotated[str | None, Header(alias="X-Pulse-Internal-Token")] = None,
    ) -> None:
        import hmac

        expected = (config.internal.service_token or "").strip()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="Internal API not configured",
            )
        provided = ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        elif x_pulse_internal_token:
            provided = x_pulse_internal_token.strip()
        if not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    return dependency


def deliver_channel_reply(
    config: AppConfig,
    *,
    reply_endpoint: dict[str, Any],
    text: str,
    messenger=None,
    session=None,
    team_id: str | None = None,
    assistant_session_id: str | None = None,
    assistant_message_id: str | None = None,
    kind: str = "final",
) -> dict[str, str]:
    dedupe_key = _dedupe_key(
        message_id=assistant_message_id,
        text=text,
        kind=kind,
    )
    channel = reply_endpoint.get("channel", "")
    if channel == "web":
        if _already_delivered(dedupe_key):
            return {"status": "sent", "reason": "deduplicated"}
        member_id = str(reply_endpoint.get("member_id", "")).strip()
        if not member_id or session is None or not team_id:
            return {"status": "queued", "reason": "missing_web_context"}
        try:
            from pulse.web.portal_chat import store_portal_chat_delivery

            store_portal_chat_delivery(
                session,
                team_id=team_id,
                member_id=member_id,
                text=text,
                kind=kind,
                assistant_session_id=assistant_session_id,
                assistant_message_id=assistant_message_id,
            )
            session.flush()
            return {"status": "sent"}
        except Exception:
            logger.exception("web channel reply store failed member_id=%s", member_id)
            return {"status": "queued", "reason": "web_store_failed"}

    if channel != "dingtalk":
        return {"status": "noop", "reason": "unsupported_channel"}

    if _already_delivered(dedupe_key):
        logger.info(
            "channel reply deduped: message_id=%s kind=%s",
            assistant_message_id,
            kind,
        )
        return {"status": "sent", "reason": "deduplicated"}

    logger.info(
        "reply.timing stage=channel_deliver_start message_id=%s kind=%s channel=%s at=%s",
        assistant_message_id,
        kind,
        channel,
        datetime.now(timezone.utc).isoformat(),
    )
    deliver_t0 = time.monotonic()

    effective_messenger = messenger if messenger is not None else _get_dingtalk_messenger(config)
    if effective_messenger is None:
        logger.warning("channel reply skipped: dingtalk messenger unavailable (check team_settings.dingtalk)")
        return {"status": "queued", "reason": "messenger_unavailable"}

    def _log_deliver_done(status: str, reason: str = "") -> dict[str, str]:
        logger.info(
            "reply.timing stage=channel_deliver_done message_id=%s kind=%s status=%s "
            "reason=%s elapsed_ms=%d",
            assistant_message_id,
            kind,
            status,
            reason,
            int((time.monotonic() - deliver_t0) * 1000),
        )
        return {"status": status, "reason": reason} if reason else {"status": status}

    conversation_type = reply_endpoint.get("conversation_type", "")
    if conversation_type == "private":
        user_id = str(reply_endpoint.get("user_id", "")).strip()
        if not user_id:
            return _log_deliver_done("queued", "missing_user_id")
        try:
            effective_messenger.send_oto_text(user_id, text)
            return _log_deliver_done("sent")
        except Exception:
            logger.exception("private reply failed for user_id=%s", user_id)
            return _log_deliver_done("queued", "private_send_failed")

    if conversation_type == "group":
        conversation_id = str(reply_endpoint.get("conversation_id", "")).strip()
        configured = (config.dingtalk.group_open_conversation_id or "").strip()
        if not conversation_id or not configured or conversation_id != configured:
            logger.warning(
                "group reply skipped: conversation_id=%s configured=%s",
                conversation_id,
                configured,
            )
            return _log_deliver_done("queued", "group_not_configured")
        try:
            effective_messenger.send_group_text(text)
            return _log_deliver_done("sent")
        except Exception:
            logger.exception("group reply failed for conversation_id=%s", conversation_id)
            return _log_deliver_done("queued", "group_send_failed")

    return _log_deliver_done("noop", "unknown_conversation_type")


def register_internal_channel_routes(app, config: AppConfig, get_db, team_repo_fn) -> None:
    require_internal_service = _require_internal_service(config)

    @app.post(
        "/api/internal/v1/channel/reply",
        dependencies=[Depends(require_internal_service)],
    )
    def internal_channel_reply(body: ChannelReplyBody, session: Session = Depends(get_db)):
        from pulse.web.settings_store import effective_config

        team, _repo = team_repo_fn(session)
        runtime = effective_config(config, session, team.id)
        result = deliver_channel_reply(
            runtime,
            reply_endpoint=body.reply_endpoint,
            text=body.text,
            session=session,
            team_id=team.id,
            assistant_session_id=body.session_id,
            assistant_message_id=body.message_id,
            kind=body.kind,
        )
        if result.get("status") != "sent":
            logger.warning(
                "channel reply not delivered: status=%s reason=%s endpoint=%s",
                result.get("status"),
                result.get("reason"),
                body.reply_endpoint,
            )
        else:
            session.commit()
        return result
