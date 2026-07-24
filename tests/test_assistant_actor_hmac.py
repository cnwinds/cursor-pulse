"""HMAC-signed Pulse actor claims for Assistant APIs."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db
from pulse.web.assistant_actor import sign_actor_headers
from tests.assistant_actor_helpers import signed_actor_headers

SERVICE_TOKEN = "assistant-hmac-secret"
TEAM_ID = "team-hmac"


def _unsigned_headers(*, permissions: str = "assistant:sessions:read:all") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Assistant-Token": SERVICE_TOKEN,
        "X-Pulse-Actor-Member-Id": "m1",
        "X-Pulse-Actor-Role": "owner",
        "X-Pulse-Actor-Permissions": permissions,
    }


@pytest.fixture
def client():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    sf = init_assistant_db("sqlite://")
    session = sf()
    attach_user_message(
        session,
        IncomingMessageEvent(
            event_id=str(uuid.uuid4()),
            channel="dingtalk",
            channel_message_id="m-1",
            assistant_id="xiaomai",
            team_id=TEAM_ID,
            sender_channel_user_id="u1",
            sender_display_name="Alice",
            conversation_type="private",
            conversation_id="u1",
            text_redacted="hello",
            occurred_at=datetime.now(timezone.utc),
        ),
    )
    session.commit()
    session.close()
    return TestClient(create_assistant_app(cfg, sf))


def test_unsigned_actor_headers_rejected(client):
    res = client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=_unsigned_headers(),
    )
    assert res.status_code == 403


def test_signed_actor_headers_allowed(client):
    res = client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=signed_actor_headers(SERVICE_TOKEN),
    )
    assert res.status_code == 200


def test_tampered_permissions_rejected(client):
    headers = signed_actor_headers(
        SERVICE_TOKEN,
        permissions="assistant:sessions:read:all",
    )
    headers["X-Pulse-Actor-Permissions"] = "assistant:sessions:delete:self"
    res = client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=headers,
    )
    assert res.status_code == 403


def test_expired_timestamp_rejected(client):
    old_ts = int(time.time()) - 301
    res = client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=signed_actor_headers(SERVICE_TOKEN, ts=old_ts),
    )
    assert res.status_code == 403


def test_sign_actor_headers_includes_ts_and_signature():
    actor = sign_actor_headers(
        SERVICE_TOKEN,
        "mem-1",
        "owner",
        "u1",
        "assistant:sessions:read:all",
        ts=1_700_000_000,
    )
    assert actor["X-Pulse-Actor-Ts"] == "1700000000"
    assert len(actor["X-Pulse-Actor-Signature"]) == 64
