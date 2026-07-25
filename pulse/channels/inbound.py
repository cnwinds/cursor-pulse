"""Channel-neutral inbound message model + shared text-command dispatch.

Any inbound channel (DingTalk, Feishu, web, ...) can normalize its native
event into an :class:`InboundMessage` and hand it to
:func:`dispatch_text_command` to run the same plain-text command handlers
(`pulse.channels.commands` / `commands_common` / `commands_loans`) keyed off
``(channel, channel_user_id)`` instead of any particular SDK's message type.

This intentionally reuses the pre-AgentRuntime "exact command → reply text"
handlers rather than the full Assistant-mirror / capability-invoke pipeline:
those handlers return ready-to-send strings (no LLM-facing result-formatting
step), which makes them safe to call synchronously from any channel runtime,
including ones (like Feishu MVP) that don't have the Assistant service wired
up yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pulse.channels.base import ChannelMessenger
from pulse.channels.commands import (
    _handle_quota_command,
    _looks_like_help,
    build_bot_help_message,
    handle_bind_cursor_command,
    handle_unbind_cursor_command,
)
from pulse.channels.commands_common import channel_admin
from pulse.channels.commands_loans import handle_key_loan_commands
from pulse.config import AppConfig
from pulse.tenant.context import team_repository

logger = logging.getLogger(__name__)


@dataclass
class InboundMessage:
    """Normalized inbound text message, independent of the source channel."""

    channel: str  # dingtalk | feishu | web
    channel_user_id: str
    display_name: str
    text: str
    conversation_type: str  # "oto" | "group"
    conversation_id: str | None = None
    message_id: str | None = None
    raw: Any = None


def dispatch_text_command(
    *,
    config: AppConfig,
    session_factory,
    messenger: ChannelMessenger,
    inbound: InboundMessage,
) -> str | None:
    """Route exact/structured text commands through the shared command path.

    Returns the reply text, or ``None`` when the text isn't a recognized
    command (caller should fall through to whatever else it does — LLM
    routing, Assistant mirror, or simply drop the message).

    ``messenger`` isn't used directly here (the handlers return plain text
    for the caller to send back on whichever transport is appropriate); it's
    accepted so callers have a uniform signature and so future command
    handlers that need to proactively push messages (e.g. multi-recipient
    admin notices) can be wired in without changing this function's shape.
    """
    text = (inbound.text or "").strip()
    if not text:
        return None

    session = session_factory()
    try:
        _team, repo = team_repository(session, config)
        user_id = inbound.channel_user_id
        display_name = inbound.display_name

        identity_channel = inbound.channel or "dingtalk"
        if _looks_like_help(text):
            reply: str | None = build_bot_help_message()
        else:
            reply = _handle_quota_command(
                text,
                user_id,
                config,
                repo,
                display_name=display_name,
                channel=identity_channel,
            )
            if reply is None:
                reply = handle_bind_cursor_command(
                    text,
                    user_id,
                    config,
                    repo,
                    display_name=display_name,
                    channel=identity_channel,
                )
            if reply is None:
                reply = handle_unbind_cursor_command(
                    text,
                    user_id,
                    config,
                    repo,
                    display_name=display_name,
                    channel=identity_channel,
                )
            if reply is None:
                is_admin = channel_admin(
                    user_id, config, repo, channel=identity_channel
                )
                reply = handle_key_loan_commands(
                    text,
                    user_id,
                    config,
                    repo,
                    is_admin=is_admin,
                    display_name=display_name,
                    channel=identity_channel,
                )

        repo.commit()
        return reply
    except Exception:
        session.rollback()
        logger.exception(
            "dispatch_text_command failed channel=%s channel_user_id=%s",
            inbound.channel,
            inbound.channel_user_id,
        )
        return "命令执行失败，请稍后重试或联系管理员。"
    finally:
        session.close()
