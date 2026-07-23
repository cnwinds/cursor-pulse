from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-prompts-api"


def _headers(*, permissions: str = "assistant:prompts:read") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Permissions": permissions,
    }


@pytest.fixture
def client() -> TestClient:
    config = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    session_factory = init_assistant_db("sqlite://", team_id=TEAM_ID)
    return TestClient(create_assistant_app(config, session_factory))


def test_prompts_list_requires_read_permission(client: TestClient):
    response = client.get(
        "/api/assistant/v1/prompts",
        headers=_headers(permissions="assistant:sessions:read:self"),
    )

    assert response.status_code == 403
