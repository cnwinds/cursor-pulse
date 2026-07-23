from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.conversation.turn_inbox import try_schedule_next_turn, is_turn_running
from assistant_platform.conversation.turn_recovery import recover_stale_turn_if_needed
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.secrets.redact import redact_text
from assistant_platform.storage.models import IncomingEventRow
from assistant_platform.storage.repository import AssistantRepository

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    created: bool
    event_row_id: str
    text_redacted: str
    duplicate: bool = False
    session_id: str | None = None
    message_id: str | None = None


class EventIngestService:
    def __init__(self, session: Session, *, turn_timeout_seconds: int = 300):
        self.repo = AssistantRepository(session)
        self.turn_timeout_seconds = turn_timeout_seconds

    def ingest(self, event: IncomingMessageEvent) -> IngestResult:
        self.repo.ensure_assistant(event.assistant_id)

        existing = self.repo.find_by_channel_message(event.channel, event.channel_message_id)
        if existing is not None:
            self.repo.add_audit(
                assistant_id=event.assistant_id,
                team_id=event.team_id,
                action="event.ingest.duplicate",
                detail=event.channel_message_id,
            )
            return IngestResult(
                created=False,
                event_row_id=existing.id,
                text_redacted=existing.text_redacted,
                duplicate=True,
            )

        text, refs = redact_text(event.text_redacted or "")
        safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
        if event.secret_refs:
            for r in event.secret_refs:
                safe_refs.append({k: r[k] for k in ("ref_id", "kind", "hint") if k in r})

        row = IncomingEventRow(
            event_id=event.event_id,
            channel=event.channel,
            channel_message_id=event.channel_message_id,
            assistant_id=event.assistant_id,
            team_id=event.team_id,
            sender_channel_user_id=event.sender_channel_user_id,
            sender_display_name=event.sender_display_name,
            conversation_type=event.conversation_type,
            conversation_id=event.conversation_id,
            reply_endpoint_json=event.reply_endpoint,
            text_redacted=text,
            secret_refs_json=safe_refs,
            attachments_json=event.attachments,
            occurred_at=event.occurred_at or datetime.now(timezone.utc),
            raw_metadata_json=event.raw_metadata_redacted,
        )
        saved = self.repo.add_incoming(row)
        event_for_session = replace(event, text_redacted=text, secret_refs=safe_refs)
        session_row, message_row = attach_user_message(
            self.repo.session,
            event_for_session,
            incoming_event_id=saved.id,
        )
        recover_stale_turn_if_needed(
            self.repo.session,
            session_row,
            timeout_seconds=self.turn_timeout_seconds,
        )
        self.repo.add_outbox(
            assistant_id=event.assistant_id,
            team_id=event.team_id,
            kind="event.received",
            payload={"incoming_event_id": saved.id, "channel_message_id": event.channel_message_id},
        )
        scheduled = try_schedule_next_turn(self.repo.session, session_row, self.repo)
        if scheduled:
            logger.info(
                "reply.timing stage=ingest_queued session_id=%s message_id=%s "
                "channel_message_id=%s at=%s",
                session_row.id,
                message_row.id,
                event.channel_message_id,
                datetime.now(timezone.utc).isoformat(),
            )
            self.repo.add_audit(
                assistant_id=event.assistant_id,
                team_id=event.team_id,
                action="event.ingest.created",
                detail=event.channel_message_id,
                meta={"incoming_event_id": saved.id},
            )
        elif is_turn_running(session_row):
            self.repo.add_audit(
                assistant_id=event.assistant_id,
                team_id=event.team_id,
                action="event.ingest.inbox",
                detail=event.channel_message_id,
                meta={
                    "incoming_event_id": saved.id,
                    "session_id": session_row.id,
                    "message_id": message_row.id,
                },
            )
        return IngestResult(
            created=True,
            event_row_id=saved.id,
            text_redacted=text,
            session_id=session_row.id,
            message_id=message_row.id,
        )
