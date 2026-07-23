import uuid

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db


@pytest.fixture
def client():
    cfg = AssistantConfig(service_token="secret", team_id="t1")
    sf = init_assistant_db("sqlite://")
    app = create_assistant_app(cfg, sf)
    return TestClient(app)


def test_health(client):
    assert client.get("/health").status_code == 200


def test_ingest_requires_token(client):
    body = {
        "event_id": str(uuid.uuid4()),
        "channel": "dingtalk",
        "channel_message_id": "mid-1",
        "assistant_id": "xiaomai",
        "team_id": "t1",
        "sender_channel_user_id": "u1",
        "conversation_type": "private",
        "conversation_id": "u1",
        "text_redacted": "hi",
    }
    assert client.post("/api/assistant/v1/events/messages", json=body).status_code == 401
    ok = client.post(
        "/api/assistant/v1/events/messages",
        json=body,
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200
    assert ok.json()["created"] is True
    again = client.post(
        "/api/assistant/v1/events/messages",
        json=body,
        headers={"X-Assistant-Token": "secret"},
    )
    assert again.status_code == 200
    assert again.json()["created"] is False


def test_ingest_rejects_unconfigured_service_token():
    cfg = AssistantConfig(service_token="", team_id="t1")
    sf = init_assistant_db("sqlite://")
    app = create_assistant_app(cfg, sf)
    client = TestClient(app)
    body = {
        "event_id": str(uuid.uuid4()),
        "channel": "dingtalk",
        "channel_message_id": "mid-unconfigured",
        "assistant_id": "xiaomai",
        "team_id": "t1",
        "sender_channel_user_id": "u1",
        "conversation_type": "private",
        "conversation_id": "u1",
        "text_redacted": "hi",
    }
    response = client.post(
        "/api/assistant/v1/events/messages",
        json=body,
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
