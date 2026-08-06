"""HTTP API for recording Pulse-originated outbound messages in the ledger."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.conversation.outbound_ledger import record_outbound_message
from assistant_platform.domain.identity import DEFAULT_ASSISTANT_ID


class OutboundLedgerBody(BaseModel):
    team_id: str
    channel: str
    conversation_type: str
    text: str
    source: str
    user_id: str | None = None
    conversation_id: str | None = None
    kind: str = "notify"
    assistant_id: str = DEFAULT_ASSISTANT_ID


def register_outbound_ledger_routes(
    app,
    *,
    session_factory: sessionmaker[Session],
    require_service_token: Callable[..., None],
) -> None:
    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.post(
        "/api/assistant/v1/ledger/outbound",
        dependencies=[Depends(require_service_token)],
    )
    def ledger_outbound(
        body: OutboundLedgerBody,
        session: Session = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            session_row, message_row = record_outbound_message(
                session,
                team_id=body.team_id,
                channel=body.channel,
                conversation_type=body.conversation_type,
                text=body.text,
                source=body.source,
                user_id=body.user_id,
                conversation_id=body.conversation_id,
                kind=body.kind,
                assistant_id=body.assistant_id or DEFAULT_ASSISTANT_ID,
            )
            session.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "recorded",
            "session_id": session_row.id,
            "message_id": message_row.id,
            "text_redacted": message_row.text_redacted,
            "meta_json": message_row.meta_json,
        }
