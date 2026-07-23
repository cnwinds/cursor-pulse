import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.ingest.service import EventIngestService
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.storage.models import (
    AuditEventRow,
    BackgroundJobRow,
    IncomingEventRow,
    OutboxEventRow,
)


def _event(msg_id: str, text: str = "hello") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id="team-1",
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def test_ingest_is_idempotent_on_channel_message_id():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    first = svc.ingest(_event("m-1", "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz0123"))
    second = svc.ingest(_event("m-1", "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz0123"))
    session.commit()
    assert first.created is True
    assert second.created is False
    assert first.event_row_id == second.event_row_id
    assert "crsr_abcdefghijklmnopqrstuvwxyz0123" not in first.text_redacted


def test_different_message_ids_create_two_rows():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    a = svc.ingest(_event("m-a"))
    b = svc.ingest(_event("m-b"))
    session.commit()
    assert a.created and b.created
    assert a.event_row_id != b.event_row_id


def test_ingest_creates_audit_outbox_job():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    result = svc.ingest(_event("m-audit"))
    session.commit()

    audit = session.scalar(
        select(AuditEventRow).where(AuditEventRow.action == "event.ingest.created")
    )
    assert audit is not None
    assert audit.detail == "m-audit"
    assert audit.meta_json.get("incoming_event_id") == result.event_row_id

    outbox = session.scalar(
        select(OutboxEventRow).where(OutboxEventRow.kind == "event.received")
    )
    assert outbox is not None
    assert outbox.payload_json["incoming_event_id"] == result.event_row_id
    assert outbox.payload_json["channel_message_id"] == "m-audit"

    job = session.scalar(
        select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
    )
    assert job is not None
    assert job.payload_json["incoming_event_id"] == result.event_row_id
    assert job.payload_json["session_id"]
    assert job.payload_json["message_id"]

    chat_session = session.scalar(select(ChatSessionRow))
    assert chat_session is not None
    user_message = session.scalar(
        select(ChatMessageRow).where(ChatMessageRow.role == "user")
    )
    assert user_message is not None
    assert user_message.incoming_event_id == result.event_row_id
    assert user_message.session_id == chat_session.id


def test_secret_refs_json_never_contains_secret_field():
    plaintext = "crsr_abcdefghijklmnopqrstuvwxyz0123"
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    result = svc.ingest(_event("m-secret", f"绑定 cursor key {plaintext}"))
    session.commit()

    row = session.get(IncomingEventRow, result.event_row_id)
    assert row is not None
    assert len(row.secret_refs_json) >= 1
    for ref in row.secret_refs_json:
        assert "secret" not in ref
        for value in ref.values():
            if isinstance(value, str):
                assert plaintext not in value


def test_duplicate_ingest_writes_duplicate_audit_no_second_outbox():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    svc.ingest(_event("m-dup"))
    svc.ingest(_event("m-dup"))
    session.commit()

    audits = list(session.scalars(select(AuditEventRow)))
    created_audits = [a for a in audits if a.action == "event.ingest.created"]
    duplicate_audits = [a for a in audits if a.action == "event.ingest.duplicate"]
    assert len(created_audits) == 1
    assert len(duplicate_audits) == 1
    assert duplicate_audits[0].detail == "m-dup"

    outbox_count = session.scalar(
        select(func.count()).select_from(OutboxEventRow).where(
            OutboxEventRow.kind == "event.received"
        )
    )
    assert outbox_count == 1

    message_count = session.scalar(select(func.count()).select_from(ChatMessageRow))
    assert message_count == 1


def test_ingest_routes_to_inbox_when_turn_running():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    first = svc.ingest(_event("m-first", "查询用量"))
    session.commit()

    jobs_after_first = list(
        session.scalars(
            select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
        )
    )
    assert len(jobs_after_first) == 1

    chat_session = session.scalar(select(ChatSessionRow))
    assert chat_session is not None
    from assistant_platform.conversation.turn_inbox import is_turn_running

    assert is_turn_running(chat_session)

    second = svc.ingest(_event("m-second", "查6月份的"))
    session.commit()
    assert second.created is True

    jobs_after_second = list(
        session.scalars(
            select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
        )
    )
    assert len(jobs_after_second) == 1

    inbox_audit = session.scalar(
        select(AuditEventRow).where(AuditEventRow.action == "event.ingest.inbox")
    )
    assert inbox_audit is not None
    assert inbox_audit.detail == "m-second"

    session.refresh(chat_session)
    pending = list(
        session.scalars(
            select(ChatMessageRow).where(
                ChatMessageRow.role == "user",
                ChatMessageRow.handled_at.is_(None),
            )
        )
    )
    assert len(pending) == 1
    assert pending[0].text_redacted == "查6月份的"

    user_messages = list(
        session.scalars(select(ChatMessageRow).where(ChatMessageRow.role == "user"))
    )
    assert len(user_messages) == 2
