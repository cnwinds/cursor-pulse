from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatMessageRow
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.memory.archive_pipeline import run_archive_pipeline
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import AuditEventRow
from assistant_platform.memory.semantic.models import SemanticAtomRow

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-memory-api"


def _headers(
    *,
    permissions: str = (
        "assistant:sessions:read:self,"
        "assistant:sessions:export:self,"
        "assistant:sessions:delete:self"
    ),
    channel_user_id: str = "u1",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Channel-User-Id": channel_user_id,
        "X-Pulse-Actor-Permissions": permissions,
    }


def _event(*, sender: str = "u1", text: str = "alpha project deadline Friday") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id=sender,
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id=sender,
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def _archived_session(sf, *, text: str = "alpha project deadline Friday"):
    session = sf()
    session_row, _ = attach_user_message(session, _event(text=text))
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="noted alpha project",
            meta_json={"kind": "final"},
        )
    )
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    session.commit()
    config = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True)
    run_archive_pipeline(session, config=config, session_row=session_row)
    session.commit()
    session_id = session_row.id
    session.close()
    return session_id


@pytest.fixture
def client():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True)
    sf = init_assistant_db("sqlite://", team_id=TEAM_ID)
    app = create_assistant_app(cfg, sf)
    return TestClient(app), sf


def test_memories_list_and_search(client):
    test_client, sf = client
    session_id = _archived_session(sf, text="alpha project rocket launch")

    list_resp = test_client.get(
        "/api/assistant/v1/memories",
        params={"team_id": TEAM_ID, "user_id": "u1"},
        headers=_headers(),
    )
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert any(row["session_id"] == session_id for row in body["archives"])

    search_resp = test_client.get(
        "/api/assistant/v1/memories/search",
        params={"team_id": TEAM_ID, "user_id": "u1", "query": "alpha project"},
        headers=_headers(),
    )
    assert search_resp.status_code == 200
    fragments = search_resp.json()["fragments"]
    assert fragments
    assert "alpha project" in fragments[0]["text"].lower()


def test_memories_expand_and_summary(client):
    test_client, sf = client
    session_id = _archived_session(sf)

    search_resp = test_client.get(
        "/api/assistant/v1/memories/search",
        params={"team_id": TEAM_ID, "user_id": "u1", "query": "alpha"},
        headers=_headers(),
    )
    hit = search_resp.json()["fragments"][0]
    expand_resp = test_client.post(
        "/api/assistant/v1/memories/expand",
        headers=_headers(),
        json={
            "team_id": TEAM_ID,
            "user_id": "u1",
            "session_id": hit["session_id"],
            "chunk_index": hit["chunk_index"],
            "start_seq": hit["start_seq"],
            "end_seq": hit["end_seq"],
        },
    )
    assert expand_resp.status_code == 200
    assert expand_resp.json()["anchor"]["session_id"] == session_id

    summary_resp = test_client.get(
        f"/api/assistant/v1/memories/sessions/{session_id}/summary",
        params={"team_id": TEAM_ID, "user_id": "u1"},
        headers=_headers(),
    )
    assert summary_resp.status_code == 200
    assert summary_resp.json()["session_id"] == session_id


def test_memories_export(client):
    test_client, sf = client
    _archived_session(sf)
    response = test_client.get(
        "/api/assistant/v1/memories/export",
        params={"team_id": TEAM_ID, "user_id": "u1"},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == TEAM_ID
    assert body["archives"]
    assert "exported_at" in body


def test_memory_opt_out_blocks_new_archive(client):
    test_client, sf = client
    opt_resp = test_client.post(
        "/api/assistant/v1/memories/opt-out",
        headers=_headers(),
        json={"user_id": "u1", "team_id": TEAM_ID},
    )
    assert opt_resp.status_code == 200
    assert opt_resp.json()["opted_out"] is True

    session = sf()
    session_row, _ = attach_user_message(session, _event(text="should not archive"))
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="ok",
            meta_json={"kind": "final"},
        )
    )
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    session.commit()
    config = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID, memory_enabled=True)
    with pytest.raises(RuntimeError, match="opt-out"):
        run_archive_pipeline(session, config=config, session_row=session_row)
    session.close()

    status = test_client.get(
        "/api/assistant/v1/memories/opt-out",
        params={"team_id": TEAM_ID, "user_id": "u1"},
        headers=_headers(),
    )
    assert status.json()["opted_out"] is True


def test_delete_session_memory_cascades(client):
    test_client, sf = client
    session_id = _archived_session(sf, text="cascade delete target phrase")

    delete_resp = test_client.delete(
        f"/api/assistant/v1/memories/sessions/{session_id}",
        params={"team_id": TEAM_ID, "user_id": "u1"},
        headers=_headers(),
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["archives_removed"] == 1

    db = sf()
    assert db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)) is None
    fts_count = db.scalar(
        text("SELECT COUNT(*) FROM ap_archive_chunks_fts WHERE session_id = :sid"),
        {"sid": session_id},
    )
    assert fts_count == 0
    search_resp = test_client.get(
        "/api/assistant/v1/memories/search",
        params={"team_id": TEAM_ID, "user_id": "u1", "query": "cascade delete"},
        headers=_headers(),
    )
    assert search_resp.json()["fragments"] == []
    db.close()


def test_delete_all_personal_memory(client):
    test_client, sf = client
    _archived_session(sf, text="personal memory one")
    _archived_session(sf, text="personal memory two")

    response = test_client.delete(
        "/api/assistant/v1/memories/all",
        params={"team_id": TEAM_ID, "user_id": "u1"},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["sessions_processed"] >= 2

    db = sf()
    remaining = db.scalars(
        select(SessionArchiveRow).where(
            SessionArchiveRow.team_id == TEAM_ID,
            SessionArchiveRow.subject_id == "u1",
        )
    ).all()
    assert not remaining
    db.close()


def test_delete_memory_item_atom(client):
    test_client, sf = client
    _archived_session(sf, text="fact: project Pulse uses SQLite")

    db = sf()
    atom = db.scalar(select(SemanticAtomRow).where(SemanticAtomRow.subject_id == "u1"))
    if atom is None:
        db.close()
        pytest.skip("no atom distilled for this fixture")
    atom_id = atom.id
    db.close()

    response = test_client.delete(
        f"/api/assistant/v1/memories/items/{atom_id}",
        params={"team_id": TEAM_ID, "user_id": "u1", "source_type": "atom"},
        headers=_headers(),
    )
    assert response.status_code == 200
    verify = sf()
    assert verify.get(SemanticAtomRow, atom_id) is None
    verify.close()


def test_memories_self_scope_denied_for_other_user(client):
    test_client, sf = client
    _archived_session(sf)
    response = test_client.get(
        "/api/assistant/v1/memories",
        params={"team_id": TEAM_ID, "user_id": "u2"},
        headers=_headers(channel_user_id="u1"),
    )
    assert response.status_code == 403


def test_session_delete_cascades_archive(client):
    test_client, sf = client
    session_id = _archived_session(sf, text="session delete cascade phrase")

    response = test_client.delete(
        f"/api/assistant/v1/sessions/{session_id}",
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["memory_purged"] is True

    db = sf()
    assert db.scalar(select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)) is None
    audit = db.scalars(select(AuditEventRow).where(AuditEventRow.action == "session.deleted")).all()
    assert audit
    assert audit[-1].meta_json.get("archives_removed", 0) >= 1
    db.close()
