from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.storage.models import (
    AssistantRow,
    AuditEventRow,
    BackgroundJobRow,
    IncomingEventRow,
    OutboxEventRow,
)


class AssistantRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure_assistant(self, assistant_id: str, display_name: str = "小脉") -> None:
        row = self.session.get(AssistantRow, assistant_id)
        if row is None:
            self.session.add(AssistantRow(id=assistant_id, display_name=display_name))
            self.session.flush()

    def find_by_channel_message(self, channel: str, channel_message_id: str) -> IncomingEventRow | None:
        return self.session.scalar(
            select(IncomingEventRow).where(
                IncomingEventRow.channel == channel,
                IncomingEventRow.channel_message_id == channel_message_id,
            )
        )

    def add_incoming(self, row: IncomingEventRow) -> IncomingEventRow:
        self.session.add(row)
        self.session.flush()
        return row

    def add_audit(self, *, assistant_id: str, team_id: str, action: str, detail: str = "", meta: dict | None = None) -> None:
        self.session.add(
            AuditEventRow(
                assistant_id=assistant_id,
                team_id=team_id,
                action=action,
                detail=detail,
                meta_json=meta or {},
            )
        )

    def add_outbox(self, *, assistant_id: str, team_id: str, kind: str, payload: dict) -> OutboxEventRow:
        row = OutboxEventRow(
            assistant_id=assistant_id,
            team_id=team_id,
            kind=kind,
            payload_json=payload,
            status="pending",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_job(self, *, job_type: str, payload: dict) -> BackgroundJobRow:
        if job_type == "reply.send":
            message_id = payload.get("message_id")
            if message_id:
                existing = self.find_reply_send_job(message_id=str(message_id))
                if existing is not None:
                    return existing
        if job_type == "session.process":
            message_id = payload.get("message_id")
            if message_id:
                existing = self.find_session_process_job(message_id=str(message_id))
                if existing is not None:
                    return existing
        row = BackgroundJobRow(job_type=job_type, payload_json=payload, status="pending")
        self.session.add(row)
        self.session.flush()
        return row

    def find_session_process_job(self, *, message_id: str) -> BackgroundJobRow | None:
        jobs = self.session.scalars(
            select(BackgroundJobRow).where(
                BackgroundJobRow.job_type == "session.process",
                BackgroundJobRow.status.in_(("pending", "processing", "done")),
            )
        ).all()
        for job in jobs:
            if str(job.payload_json.get("message_id") or "") == message_id:
                return job
        return None

    def find_reply_send_job(self, *, message_id: str) -> BackgroundJobRow | None:
        jobs = self.session.scalars(
            select(BackgroundJobRow).where(
                BackgroundJobRow.job_type == "reply.send",
                BackgroundJobRow.status.in_(("pending", "processing", "done")),
            )
        ).all()
        for job in jobs:
            if str(job.payload_json.get("message_id") or "") == message_id:
                return job
        return None
