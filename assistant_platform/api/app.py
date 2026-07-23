from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.api.auth import build_require_service_token
from assistant_platform.api.capabilities import register_capability_routes
from assistant_platform.api.evaluations import register_evaluation_routes
from assistant_platform.api.memories import register_memory_routes
from assistant_platform.api.profiles import register_profile_routes
from assistant_platform.api.prompts import register_prompt_routes
from assistant_platform.api.sessions import register_session_routes
from assistant_platform.api.skills_admin import register_skills_admin_routes
from assistant_platform.config import AssistantConfig
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.ingest.service import EventIngestService


class IncomingEventBody(BaseModel):
    event_id: str
    channel: str
    channel_message_id: str
    assistant_id: str
    team_id: str
    sender_channel_user_id: str
    sender_display_name: str = ""
    conversation_type: str
    conversation_id: str
    reply_endpoint: dict[str, Any] = Field(default_factory=dict)
    text_redacted: str = ""
    secret_refs: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    occurred_at: datetime | None = None
    raw_metadata_redacted: dict[str, Any] = Field(default_factory=dict)


def create_assistant_app(config: AssistantConfig, session_factory: sessionmaker[Session]) -> FastAPI:
    app = FastAPI(title="Assistant Platform", version="0.1.0")

    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    require_service_token = build_require_service_token(config)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "assistant_platform", "time": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/assistant/v1/events/messages", dependencies=[Depends(require_service_token)])
    def ingest_message(body: IncomingEventBody, session: Session = Depends(get_db)):
        event = IncomingMessageEvent(**body.model_dump())
        result = EventIngestService(
            session,
            turn_timeout_seconds=config.llm.turn_timeout_seconds,
        ).ingest(event)
        session.commit()
        return {
            "created": result.created,
            "duplicate": result.duplicate,
            "event_row_id": result.event_row_id,
            "text_redacted": result.text_redacted,
            "session_id": result.session_id,
            "message_id": result.message_id,
        }

    register_capability_routes(
        app,
        config=config,
        session_factory=session_factory,
        require_service_token=require_service_token,
    )
    register_session_routes(
        app,
        session_factory=session_factory,
        require_service_token=require_service_token,
    )
    register_memory_routes(
        app,
        config=config,
        session_factory=session_factory,
        require_service_token=require_service_token,
    )
    register_profile_routes(
        app,
        session_factory=session_factory,
        require_service_token=require_service_token,
    )
    register_prompt_routes(
        app,
        session_factory=session_factory,
        require_service_token=require_service_token,
    )
    register_skills_admin_routes(
        app,
        require_service_token=require_service_token,
    )
    register_evaluation_routes(
        app,
        session_factory=session_factory,
        require_service_token=require_service_token,
    )

    return app
