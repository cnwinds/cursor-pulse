from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-prompts-readonly-api"


def _headers(*, permissions: str = "assistant:prompts:read") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Permissions": permissions,
    }


@pytest.fixture
def client() -> TestClient:
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    session_factory = init_assistant_db("sqlite://", team_id=TEAM_ID)
    return TestClient(create_assistant_app(cfg, session_factory))


def test_prompts_list_from_files(client: TestClient):
    response = client.get("/api/assistant/v1/prompts", headers=_headers())

    assert response.status_code == 200
    fragments = response.json()["fragments"]
    heart = next(fragment for fragment in fragments if fragment["key"] == "heart.md")
    assert heart == {
        "key": "heart.md",
        "path": "docs/heart.md",
        "description": "人设与基本语气",
        "content_preview": "你是小脉，团队内的 Cursor 使用助手。",
    }


def test_prompt_preview_comes_from_files(client: TestClient):
    response = client.get("/api/assistant/v1/prompts/preview", headers=_headers())

    assert response.status_code == 200
    assert "## heart.md" in response.json()["markdown"]
    assert "你是小脉，团队内的 Cursor 使用助手。" in response.json()["markdown"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/assistant/v1/prompts/fragments",
        "/api/assistant/v1/prompts/releases",
        "/api/assistant/v1/prompts/releases/release-1/canary",
        "/api/assistant/v1/prompts/releases/release-1/promote",
        "/api/assistant/v1/prompts/releases/release-1/rollback",
    ],
)
def test_prompt_write_returns_410(client: TestClient, path: str):
    response = client.post(path, json={"key": "x", "content": "y"}, headers=_headers())

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Prompt editing retired; edit files in assistant_platform/prompts/docs"
    )
