from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db
from tests.assistant_actor_helpers import signed_actor_headers

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-skills-history"


def _headers() -> dict[str, str]:
    return signed_actor_headers(
        SERVICE_TOKEN,
        member_id="mem-1",
        role="operator",
        permissions="assistant:skills:read",
    )


@pytest.fixture
def client() -> TestClient:
    cfg = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    session_factory = init_assistant_db("sqlite://", team_id=TEAM_ID)
    return TestClient(create_assistant_app(cfg, session_factory))


def test_skill_file_history_lists_working_and_commits(client: TestClient):
    response = client.get(
        "/api/assistant/v1/skills/cursor.self/overview/file-history",
        headers=_headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["rel_path"].endswith("cursor.self/overview.md")
    refs = [item["ref"] for item in payload["items"]]
    assert refs[0] == "WORKING"
    assert len(refs) > 1


def test_skill_file_compare_returns_merge_panes(client: TestClient):
    history = client.get(
        "/api/assistant/v1/skills/cursor.self/overview/file-history",
        headers=_headers(),
    ).json()
    assert len(history["items"]) >= 2
    left_ref = history["items"][1]["ref"]
    right_ref = history["items"][0]["ref"]
    response = client.get(
        "/api/assistant/v1/skills/cursor.self/overview/file-compare",
        params={"left_ref": left_ref, "right_ref": right_ref},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert "left_lines" in body
    assert "right_lines" in body
    assert "result_text" in body
