from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatMessageRow
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.evolution.clustering import cluster_low_score_reviews
from assistant_platform.evolution.models import FailureClusterRow, PromptChangeProposalRow
from assistant_platform.prompts.seed import get_production_release
from assistant_platform.review.auto_review import run_auto_review
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-evolution"


def _event(*, msg_id: str | None = None) -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id or str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted="hello",
        occurred_at=datetime.now(timezone.utc),
    )


def _closed_low_score_session(session, *, tag: str = "error_messages"):
    session_row, _ = attach_user_message(session, _event())
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="",
        )
    )
    if tag == "error_messages":
        session.add(
            ChatMessageRow(
                session_id=session_row.id,
                role="error",
                text_redacted="boom",
            )
        )
    session.flush()
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    run_auto_review(session, session_row.id)
    session.commit()
    return session_row


def test_cluster_low_score_reviews_creates_cluster_and_draft_proposal():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    from tests.assistant_platform.conftest import seed_test_production_release

    seed_test_production_release(session)
    session.commit()
    session_row = _closed_low_score_session(session)
    production_before = get_production_release(session)
    assert production_before is not None

    cluster_low_score_reviews(session)
    session.commit()

    cluster = session.scalar(
        select(FailureClusterRow).where(FailureClusterRow.tag == "error_messages")
    )
    assert cluster is not None
    assert session_row.id in cluster.session_ids_json
    assert cluster.size >= 1

    proposal = session.scalar(
        select(PromptChangeProposalRow).where(
            PromptChangeProposalRow.cluster_id == cluster.id
        )
    )
    assert proposal is not None
    assert proposal.status == "draft"
    assert proposal.diff_text
    assert "error_messages" in proposal.diff_text or "precepts" in proposal.diff_text

    production_after = get_production_release(session)
    assert production_after.id == production_before.id
    session.close()


def _headers(*, permissions: str = "assistant:prompts:read,assistant:prompts:approve") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Permissions": permissions,
    }


@pytest.fixture
def api_client():
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    sf = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = sf()
    try:
        from tests.assistant_platform.conftest import seed_test_production_release

        seed_test_production_release(db)
        db.commit()
    finally:
        db.close()
    app = create_assistant_app(cfg, sf)
    return TestClient(app), sf


def test_approve_proposal_does_not_change_production_release(api_client):
    test_client, sf = api_client
    db = sf()
    try:
        _closed_low_score_session(db)
        cluster_low_score_reviews(db)
        db.commit()
        proposal = db.scalar(select(PromptChangeProposalRow))
        assert proposal is not None
        production_before = get_production_release(db)
        proposal_id = proposal.id
    finally:
        db.close()

    resp = test_client.post(
        f"/api/assistant/v1/prompts/proposals/{proposal_id}/approve",
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    db = sf()
    try:
        updated = db.get(PromptChangeProposalRow, proposal_id)
        assert updated is not None
        assert updated.status == "approved"
        production_after = get_production_release(db)
        assert production_after.id == production_before.id
    finally:
        db.close()


def test_list_clusters_and_proposals(api_client):
    test_client, sf = api_client
    db = sf()
    try:
        _closed_low_score_session(db)
        cluster_low_score_reviews(db)
        db.commit()
    finally:
        db.close()

    clusters_resp = test_client.get(
        "/api/assistant/v1/prompts/clusters",
        headers=_headers(permissions="assistant:prompts:read"),
    )
    assert clusters_resp.status_code == 200
    assert len(clusters_resp.json()["items"]) >= 1

    proposals_resp = test_client.get(
        "/api/assistant/v1/prompts/proposals",
        headers=_headers(permissions="assistant:prompts:read"),
    )
    assert proposals_resp.status_code == 200
    assert len(proposals_resp.json()["items"]) >= 1
