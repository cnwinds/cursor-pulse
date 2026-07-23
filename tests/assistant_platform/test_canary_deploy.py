from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.prompts.deploy import (
    deploy_canary,
    promote_release,
    rollback_production,
    session_in_canary_bucket,
)
from assistant_platform.prompts.models import PromptDeploymentRow, PromptFragmentRow, PromptReleaseRow
from assistant_platform.prompts.fragments import canonical_fragments
from assistant_platform.prompts.seed import DEFAULT_RELEASE_NAME, get_production_release
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-canary"


def _event(*, conversation_id: str = "u1") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id=conversation_id,
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id=conversation_id,
        text_redacted="hello",
        occurred_at=datetime.now(timezone.utc),
    )


def _ensure_test_production(session) -> PromptReleaseRow:
    production = get_production_release(session)
    if production is not None:
        return production
    fragment_ids: list[str] = []
    for stub in canonical_fragments():
        fragment = PromptFragmentRow(
            key=stub["key"],
            content=stub["content"],
            version="3",
            status="active",
        )
        session.add(fragment)
        session.flush()
        fragment_ids.append(fragment.id)
    release = PromptReleaseRow(
        name=DEFAULT_RELEASE_NAME,
        status="production",
        fragment_ids_json=fragment_ids,
    )
    session.add(release)
    session.flush()
    return release


def _draft_release(session, name: str = "v1-canary-test") -> PromptReleaseRow:
    production = _ensure_test_production(session)
    release = PromptReleaseRow(
        name=name,
        status="draft",
        fragment_ids_json=list(production.fragment_ids_json),
    )
    session.add(release)
    session.flush()
    return release


def test_session_in_canary_bucket_is_deterministic():
    session_id = "sess-deterministic-001"
    digest = int(hashlib.sha256(session_id.encode()).hexdigest(), 16)
    bucket = digest % 100
    assert session_in_canary_bucket(session_id, percent=10) == (bucket < 10)
    assert session_in_canary_bucket(session_id, percent=10) == (bucket < 10)


@pytest.mark.skip(reason="prompt release pipeline retired")
def test_new_session_pins_canary_release_when_in_bucket():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    production = get_production_release(session)
    draft = _draft_release(session)
    deploy_canary(session, draft.id, percent=100)
    session.commit()

    session_row, _ = attach_user_message(session, _event(conversation_id="canary-user"))
    session.commit()

    assert session_row.prompt_release_id == draft.id
    assert session_row.prompt_release_id != production.id
    session.close()


@pytest.mark.skip(reason="prompt release pipeline retired")
def test_new_session_pins_production_when_not_in_canary_bucket():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    production = get_production_release(session)
    draft = _draft_release(session, name="v1-canary-0pct")
    deploy_canary(session, draft.id, percent=0)
    session.commit()

    session_row, _ = attach_user_message(session, _event(conversation_id="prod-user"))
    session.commit()

    assert session_row.prompt_release_id == production.id
    session.close()


def test_promote_release_switches_production():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    production = _ensure_test_production(session)
    draft = _draft_release(session, name="v2-promote")
    session.commit()

    promote_release(session, draft.id)
    session.commit()

    new_prod = get_production_release(session)
    assert new_prod is not None
    assert new_prod.id == draft.id
    retired = session.get(PromptReleaseRow, production.id)
    assert retired is not None
    assert retired.status == "retired"
    session.close()


def test_rollback_production_restores_previous_release():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    production = _ensure_test_production(session)
    draft = _draft_release(session, name="v2-rollback")
    session.commit()

    promote_release(session, draft.id)
    session.commit()
    rollback_production(session, production.id)
    session.commit()

    current = get_production_release(session)
    assert current is not None
    assert current.id == production.id
    rolled = session.get(PromptReleaseRow, draft.id)
    assert rolled is not None
    assert rolled.status == "retired"
    session.close()


def test_deploy_canary_creates_deployment_row():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    draft = _draft_release(session, name="v1-deploy-row")
    session.commit()

    deploy_canary(session, draft.id, percent=10)
    session.commit()

    release = session.get(PromptReleaseRow, draft.id)
    assert release is not None
    assert release.status == "canary"
    deployment = session.scalar(
        select(PromptDeploymentRow).where(PromptDeploymentRow.release_id == draft.id)
    )
    assert deployment is not None
    assert deployment.percent == 10
    assert deployment.status == "active"
    session.close()


def _headers(*, permissions: str = "assistant:prompts:read,assistant:prompts:write") -> dict[str, str]:
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
    app = create_assistant_app(cfg, sf)
    return TestClient(app), sf


def test_api_canary_promote_rollback_are_retired(api_client):
    test_client, _ = api_client
    release_id = "release-1"

    for path in (
        f"/api/assistant/v1/prompts/releases/{release_id}/canary",
        f"/api/assistant/v1/prompts/releases/{release_id}/promote",
        f"/api/assistant/v1/prompts/releases/{release_id}/rollback",
    ):
        response = test_client.post(path, headers=_headers())
        assert response.status_code == 410
        assert response.json()["detail"] == (
            "Prompt editing retired; edit files in assistant_platform/prompts/docs"
        )
