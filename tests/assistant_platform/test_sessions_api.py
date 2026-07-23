from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import AuditEventRow
from assistant_platform.storage.repository import AssistantRepository

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-1"


def _headers(
    *,
    permissions: str = "assistant:sessions:read:all,assistant:sessions:export:all",
    channel_user_id: str = "u1",
    member_id: str = "mem-1",
    role: str = "operator",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": member_id,
        "X-Pulse-Actor-Role": role,
        "X-Pulse-Actor-Channel-User-Id": channel_user_id,
        "X-Pulse-Actor-Permissions": permissions,
    }


def _event(
    *,
    msg_id: str,
    sender: str = "u1",
    text: str = "hello",
) -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id=sender,
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id=sender,
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def client():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    sf = init_assistant_db("sqlite://")
    session = sf()
    session_row, _ = attach_user_message(session, _event(msg_id="m-1", text="first"))
    attach_user_message(
        session,
        _event(msg_id="m-2", sender="u2", text="other"),
    )
    session.commit()
    session.close()
    app = create_assistant_app(cfg, sf)
    return TestClient(app), sf, session_row.id


def test_sessions_list_requires_service_token(client):
    test_client, _, _ = client
    assert test_client.get("/api/assistant/v1/sessions").status_code == 401


def test_sessions_list_returns_rows(client):
    test_client, _, session_id = client
    response = test_client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2
    ids = {row["id"] for row in body["items"]}
    assert session_id in ids


def test_sessions_list_includes_first_user_text(client):
    test_client, _, session_id = client
    response = test_client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=_headers(),
    )
    assert response.status_code == 200
    by_id = {row["id"]: row for row in response.json()["items"]}
    assert by_id[session_id]["first_user_text"] == "first"
    other = next(row for row in response.json()["items"] if row["id"] != session_id)
    assert other["first_user_text"] == "other"


def test_sessions_list_truncates_long_first_user_text(client):
    test_client, sf, _ = client
    long_text = "问" * 100
    session = sf()
    session_row, _ = attach_user_message(
        session,
        _event(msg_id="m-long", sender="u3", text=long_text),
    )
    session.commit()
    session_id = session_row.id
    session.close()

    response = test_client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID, "member_user_id": "u3"},
        headers=_headers(),
    )
    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["id"] == session_id)
    assert item["first_user_text"] == ("问" * 80) + "…"
    assert len(item["first_user_text"]) == 81


def test_sessions_list_first_user_text_null_without_user_message(client):
    test_client, sf, _ = client
    session = sf()
    now = datetime.now(timezone.utc)
    empty = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u-empty",
        user_id="u-empty",
        status="open",
        opened_at=now,
        last_activity_at=now,
    )
    session.add(empty)
    session.commit()
    session_id = empty.id
    session.close()

    response = test_client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID, "member_user_id": "u-empty"},
        headers=_headers(),
    )
    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["id"] == session_id)
    assert item["first_user_text"] is None


def test_sessions_self_scope_filters_other_users(client):
    test_client, _, _ = client
    response = test_client.get(
        "/api/assistant/v1/sessions",
        params={"team_id": TEAM_ID},
        headers=_headers(
            permissions="assistant:sessions:read:self",
            channel_user_id="u1",
        ),
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["user_id"] == "u1"


def test_session_detail_includes_messages(client):
    test_client, _, session_id = client
    response = test_client.get(
        f"/api/assistant/v1/sessions/{session_id}",
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session_id
    assert len(body["messages"]) >= 1
    assert body["messages"][0]["role"] == "user"


def test_session_close(client):
    test_client, sf, session_id = client
    response = test_client.post(
        f"/api/assistant/v1/sessions/{session_id}/close",
        headers=_headers(),
        json={"reason": "manual"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    session = sf()
    row = session.get(ChatSessionRow, session_id)
    assert row.status == "closed"
    session.close()


def test_session_export(client):
    test_client, _, session_id = client
    response = test_client.get(
        f"/api/assistant/v1/sessions/{session_id}/export",
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == session_id
    assert isinstance(body["messages"], list)


def test_session_delete_redacts_messages(client):
    test_client, sf, session_id = client
    response = test_client.delete(
        f"/api/assistant/v1/sessions/{session_id}",
        headers=_headers(permissions="assistant:sessions:delete:self,assistant:sessions:read:self"),
    )
    assert response.status_code == 200
    session = sf()
    messages = session.scalars(
        select(ChatMessageRow).where(ChatMessageRow.session_id == session_id)
    ).all()
    assert messages
    assert all(m.text_redacted == "[redacted]" for m in messages)
    audit = session.scalars(
        select(AuditEventRow).where(AuditEventRow.action == "session.deleted")
    ).all()
    assert audit
    session.close()


def test_session_delete_denied_for_other_user(client):
    test_client, _, session_id = client
    response = test_client.delete(
        f"/api/assistant/v1/sessions/{session_id}",
        headers=_headers(
            permissions="assistant:sessions:delete:self,assistant:sessions:read:self",
            channel_user_id="u2",
        ),
    )
    assert response.status_code == 403
