import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")
from assistant_platform.config import (
    AssistantConfig,
    _apply_team_chat_memory_overrides,
    _load_chat_memory_config,
    resolve_effective_chat_memory,
)
from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from pulse.web.settings_store import effective_config, patch_team_setting
from tests.conftest import make_team_repo


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    db_path = tmp_path / "pulse.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.storage.database_url = db_url
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="a1", display_name="A", password="x")
    repo.commit()
    s.close()
    return TestClient(create_app(config, sf)), config, owner, team.id, sf


def test_settings_includes_chat_memory_and_web_search(settings_client):
    client, _config, owner, _team_id, _sf = settings_client
    token = create_access_token(_config, owner)
    res = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "chat_memory" in body
    assert body["chat_memory"]["archive"]["enabled"] is False
    assert "web_search" in body
    assert body["web_search"]["enabled"] is False


def test_patch_chat_memory_settings(settings_client):
    client, _config, owner, _team_id, _sf = settings_client
    token = create_access_token(_config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.patch(
        "/api/settings/chat_memory",
        headers=headers,
        json={
            "data": {
                "archive": {"enabled": True, "ledger_retention_days": 90},
                "features": {"archive_pipeline": True, "auto_recall_per_turn": True},
                "recall": {"fragment_top_k": 5, "context_token_budget": 1200},
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["chat_memory"]["archive"]["enabled"] is True
    assert body["chat_memory"]["archive"]["ledger_retention_days"] == 90
    assert body["chat_memory"]["features"]["auto_recall_per_turn"] is True
    assert body["chat_memory"]["recall"]["fragment_top_k"] == 5


def test_patch_web_search_masks_api_key(settings_client):
    client, _config, owner, _team_id, _sf = settings_client
    token = create_access_token(_config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.patch(
        "/api/settings/web_search",
        headers=headers,
        json={
            "data": {
                "enabled": True,
                "api_key": "tvly-test-key",
                "max_results": 7,
                "timeout_seconds": 12,
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["web_search"]["enabled"] is True
    assert body["web_search"]["api_key"] == "***"
    assert body["web_search"]["max_results"] == 7


def test_resolve_effective_chat_memory_reads_dedicated_section(settings_client):
    _client, _config, owner, team_id, sf = settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="chat_memory",
        patch={
            "archive": {"enabled": True},
            "features": {"archive_pipeline": True},
            "recall": {"fragment_top_k": 4},
        },
        member_id=owner.id,
    )
    session.commit()
    session.close()

    cfg = AssistantConfig(team_slug="test", chat_memory=_load_chat_memory_config())
    resolved = resolve_effective_chat_memory(cfg)
    assert resolved.archive.enabled is True
    assert resolved.features.archive_pipeline is True
    assert resolved.recall.fragment_top_k == 4


def test_dedicated_chat_memory_overrides_legacy_nested(settings_client):
    _client, _config, owner, team_id, sf = settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="assistant_llm",
        patch={"chat_memory": {"recall": {"fragment_top_k": 2}}},
        member_id=owner.id,
    )
    patch_team_setting(
        session,
        team_id=team_id,
        section="chat_memory",
        patch={"recall": {"fragment_top_k": 6}},
        member_id=owner.id,
    )
    session.commit()
    session.close()

    cfg = AssistantConfig(team_slug="test", chat_memory=_load_chat_memory_config())
    resolved = _apply_team_chat_memory_overrides(cfg)
    assert resolved.recall.fragment_top_k == 6


def test_effective_config_applies_web_search_team_override(settings_client):
    client, config, owner, team_id, sf = settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="web_search",
        patch={"enabled": True, "api_key": "tvly-team", "max_results": 8},
        member_id=owner.id,
    )
    session.commit()

    runtime = effective_config(config, session, team_id)
    session.close()
    assert runtime.web_search.enabled is True
    assert runtime.web_search.api_key == "tvly-team"
    assert runtime.web_search.max_results == 8

    token = create_access_token(config, owner)
    reveal = client.get(
        "/api/settings/web_search/reveal/api_key",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reveal.status_code == 200
    assert reveal.json()["value"] == "tvly-team"
