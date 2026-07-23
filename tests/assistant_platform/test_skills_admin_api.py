from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.skills.registry import SkillRegistry
from assistant_platform.storage.db import init_assistant_db

SERVICE_TOKEN = "assistant-secret"
TEAM_ID = "team-skills-api"


@pytest.fixture
def client() -> TestClient:
    config = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    session_factory = init_assistant_db("sqlite://", team_id=TEAM_ID)
    return TestClient(create_assistant_app(config, session_factory))


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def test_list_all_cards_includes_admin_skills():
    cards = SkillRegistry().list_all_cards()
    ids = {card.skill_id for card in cards}
    assert "cursor.self/overview" in ids
    assert "team.admin/overview" in ids


def test_list_doc_files_and_read_doc_file():
    registry = SkillRegistry()
    doc_files = registry.list_doc_files("cursor.self/overview")

    assert any(row["rel_path"].endswith("cursor.self/overview.md") for row in doc_files)
    assert "Cursor" in registry.read_doc_file(
        "cursor.self/overview", "assistant_platform/skills/docs/cursor.self/overview.md"
    )


def test_skills_list_endpoint_requires_service_token(client: TestClient):
    response = client.get("/api/assistant/v1/skills")
    assert response.status_code == 401


def test_skills_list_endpoint_returns_all_skills_and_docs(client: TestClient):
    response = client.get("/api/assistant/v1/skills", headers=_headers())

    assert response.status_code == 200
    skills = response.json()["skills"]
    card = next(item for item in skills if item["skill_id"] == "team.admin/overview")
    assert card["audience"] == ["admin"]
    assert card["rel_path"] == "assistant_platform/skills/docs/team.admin/overview.md"


def test_app_starts_when_doc_frontmatter_invalid(tmp_path, monkeypatch):
    bad_root = tmp_path / "skills"
    (bad_root / "docs").mkdir(parents=True)
    (bad_root / "docs" / "broken.md").write_text(
        "---\nnot: valid: yaml: [\n---\n# Broken\n", encoding="utf-8"
    )

    original_init = SkillRegistry.__init__

    def _init_with_bad_docs(self, *, root=None):
        original_init(self, root=bad_root if root is None else root)

    monkeypatch.setattr(SkillRegistry, "__init__", _init_with_bad_docs)

    config = AssistantConfig(service_token=SERVICE_TOKEN, team_id=TEAM_ID)
    session_factory = init_assistant_db("sqlite://", team_id=TEAM_ID)
    app = create_assistant_app(config, session_factory)
    client = TestClient(app)

    response = client.get("/api/assistant/v1/skills", headers=_headers())
    assert response.status_code == 500
    assert "无法加载 skill 目录" in response.json()["detail"]


def test_skill_detail_and_help_topics_endpoints(client: TestClient):
    detail_response = client.get(
        "/api/assistant/v1/skills/cursor.self/overview", headers=_headers()
    )
    topics_response = client.get("/api/assistant/v1/skills/help-topics", headers=_headers())

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["skill_id"] == "cursor.self/overview"
    assert detail["rel_path"] == "assistant_platform/skills/docs/cursor.self/overview.md"
    assert "Cursor" in detail["markdown"]
    assert topics_response.status_code == 200
    assert any(topic["topic_key"] == "my" for topic in topics_response.json()["topics"])


def test_skill_detail_endpoint_404_for_unknown_skill(client: TestClient):
    response = client.get(
        "/api/assistant/v1/skills/does-not-exist/overview", headers=_headers()
    )
    assert response.status_code == 404
