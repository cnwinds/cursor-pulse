from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.channels.base import messenger_delivered, outbound_messenger_or_none
from pulse.config import AppConfig
from pulse.util.datetime_fmt import serialize_datetime

logger = logging.getLogger(__name__)


class ChannelReplyBody(BaseModel):
    reply_endpoint: dict[str, Any] = Field(default_factory=dict)
    text: str
    session_id: str | None = None
    message_id: str | None = None
    kind: str = "final"
    stream_id: str | None = None


class ChannelStreamBody(BaseModel):
    reply_endpoint: dict[str, Any] = Field(default_factory=dict)
    stream_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = None
    text: str = ""
    done: bool = False


_recent_channel_deliveries: dict[str, float] = {}
_dedupe_lock = threading.Lock()
_DEDUPE_TTL_SECONDS = 300.0

_SUPPORTED_IM_CHANNELS = frozenset({"dingtalk", "feishu"})


def _dedupe_key(*, message_id: str | None, text: str, kind: str) -> str | None:
    if message_id:
        return f"{message_id}:{kind}"
    return None


def _prune_dedupe_locked(now: float) -> None:
    expired = [key for key, ts in _recent_channel_deliveries.items() if now - ts > _DEDUPE_TTL_SECONDS]
    for key in expired:
        _recent_channel_deliveries.pop(key, None)


def _try_begin_delivery(dedupe_key: str | None) -> bool:
    """Claim a dedupe slot. True = proceed; False = already claimed/delivered."""
    if not dedupe_key:
        return True
    now = time.monotonic()
    with _dedupe_lock:
        _prune_dedupe_locked(now)
        if dedupe_key in _recent_channel_deliveries:
            return False
        _recent_channel_deliveries[dedupe_key] = now
        return True


def _abort_delivery(dedupe_key: str | None) -> None:
    """Release a claim so a failed attempt can be retried."""
    if not dedupe_key:
        return
    with _dedupe_lock:
        _recent_channel_deliveries.pop(dedupe_key, None)


def _get_channel_messenger(config: AppConfig):
    return outbound_messenger_or_none(config)


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
    stream_id: str | None = None,
) -> dict[str, str]:
    dedupe_key = _dedupe_key(
        message_id=assistant_message_id,
        text=text,
        kind=kind,
    )
    if not _try_begin_delivery(dedupe_key):
        channel = str(reply_endpoint.get("channel", "") or "")
        if channel == "web" and stream_id and session is not None:
            from pulse.web.portal_chat import close_portal_chat_stream

            close_portal_chat_stream(session, stream_id)
        elif channel in _SUPPORTED_IM_CHANNELS:
            logger.info(
                "channel reply deduped: message_id=%s kind=%s",
                assistant_message_id,
                kind,
            )
        return {"status": "sent", "reason": "deduplicated"}

    result = _deliver_channel_reply_once(
        config,
        reply_endpoint=reply_endpoint,
        text=text,
        messenger=messenger,
        session=session,
        team_id=team_id,
        assistant_session_id=assistant_session_id,
        assistant_message_id=assistant_message_id,
        kind=kind,
        stream_id=stream_id,
    )
    if result.get("status") != "sent":
        _abort_delivery(dedupe_key)
    return result


def _deliver_channel_reply_once(
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
    stream_id: str | None = None,
) -> dict[str, str]:
    channel = str(reply_endpoint.get("channel", "") or "")
    if channel == "web":
        from pulse.web.portal_chat import close_portal_chat_stream, store_portal_chat_delivery

        member_id = str(reply_endpoint.get("member_id", "")).strip()
        if not member_id or session is None or not team_id:
            return {"status": "queued", "reason": "missing_web_context"}
        try:
            if stream_id:
                close_portal_chat_stream(session, stream_id)
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

    if channel not in _SUPPORTED_IM_CHANNELS:
        return {"status": "noop", "reason": "unsupported_channel"}

    logger.info(
        "reply.timing stage=channel_deliver_start message_id=%s kind=%s channel=%s at=%s",
        assistant_message_id,
        kind,
        channel,
        serialize_datetime(datetime.now(UTC)),
    )
    deliver_t0 = time.monotonic()

    effective_messenger = messenger if messenger is not None else _get_channel_messenger(config)
    if effective_messenger is None:
        logger.warning("channel reply skipped: messenger unavailable (BOT_PLATFORM / credentials)")
        return {"status": "queued", "reason": "messenger_unavailable"}

    def _log_deliver_done(status: str, reason: str = "") -> dict[str, str]:
        logger.info(
            "reply.timing stage=channel_deliver_done message_id=%s kind=%s status=%s reason=%s elapsed_ms=%d",
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
            result = effective_messenger.send_oto_text(user_id, text)
            if not messenger_delivered(result):
                return _log_deliver_done("queued", "private_send_skipped")
            return _log_deliver_done("sent")
        except Exception:
            logger.exception("private reply failed for user_id=%s", user_id)
            return _log_deliver_done("queued", "private_send_failed")

    if conversation_type == "group":
        conversation_id = str(reply_endpoint.get("conversation_id", "")).strip()
        if channel == "feishu":
            if not conversation_id:
                return _log_deliver_done("queued", "missing_conversation_id")
            try:
                send_chat = getattr(effective_messenger, "send_text_to_chat", None)
                if callable(send_chat):
                    result = send_chat(conversation_id, text)
                else:
                    result = effective_messenger.send_group_text(text)
                if not messenger_delivered(result):
                    return _log_deliver_done("queued", "group_send_skipped")
                return _log_deliver_done("sent")
            except Exception:
                logger.exception("group reply failed for feishu conversation_id=%s", conversation_id)
                return _log_deliver_done("queued", "group_send_failed")

        # dingtalk: only allow reply into the configured work group
        configured = (config.dingtalk.group_open_conversation_id or "").strip()
        if not conversation_id or not configured or conversation_id != configured:
            logger.warning(
                "group reply skipped: conversation_id=%s configured=%s",
                conversation_id,
                configured,
            )
            return _log_deliver_done("queued", "group_not_configured")
        try:
            result = effective_messenger.send_group_text(text)
            if not messenger_delivered(result):
                return _log_deliver_done("queued", "group_send_skipped")
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
            stream_id=body.stream_id,
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

    @app.post(
        "/api/internal/v1/channel/stream",
        dependencies=[Depends(require_internal_service)],
    )
    def internal_channel_stream(body: ChannelStreamBody, session: Session = Depends(get_db)):
        from pulse.web.portal_chat import close_portal_chat_stream, upsert_portal_chat_stream

        if str(body.reply_endpoint.get("channel", "") or "") != "web":
            return {"status": "noop", "reason": "unsupported_channel"}
        member_id = str(body.reply_endpoint.get("member_id", "")).strip()
        if not member_id:
            return {"status": "noop", "reason": "missing_member_id"}
        team, _repo = team_repo_fn(session)
        if body.done:
            close_portal_chat_stream(session, body.stream_id)
        else:
            upsert_portal_chat_stream(
                session,
                team_id=team.id,
                member_id=member_id,
                stream_id=body.stream_id,
                text=body.text,
                assistant_session_id=body.session_id,
            )
        session.commit()
        return {"status": "ok"}
