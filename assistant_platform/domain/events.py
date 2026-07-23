from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class IncomingMessageEvent:
    event_id: str
    channel: str  # dingtalk | web
    channel_message_id: str
    assistant_id: str
    team_id: str
    sender_channel_user_id: str
    sender_display_name: str
    conversation_type: str  # private | group
    conversation_id: str
    reply_endpoint: dict[str, Any] = field(default_factory=dict)
    text_redacted: str = ""
    secret_refs: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    occurred_at: datetime | None = None
    raw_metadata_redacted: dict[str, Any] = field(default_factory=dict)
