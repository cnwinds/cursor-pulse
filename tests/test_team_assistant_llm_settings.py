import pytest

pytest.importorskip("fastapi")
from assistant_platform.config import AssistantConfig, AssistantLlmConfig, load_assistant_config
from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from pulse.web.settings_store import patch_team_setting
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _assistant_llm_settings_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def settings_client(_assistant_llm_settings_app, tmp_path, monkeypatch):
    # File URL required: load_assistant_config / overrides reopen via DATABASE_URL.
    client, config, proxy = _assistant_llm_settings_app
    db_url = f"sqlite:///{(tmp_path / 'pulse.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.storage.database_url = db_url
    sf = make_test_session_factory(db_url)
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    repo.commit()
    s.close()
    return client, config, owner, team.id, sf


def test_settings_includes_assistant_llm(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    token = create_access_token(config, owner)
    res = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "assistant_llm" in body
    assert "base_url" in body["assistant_llm"]
    assert "base_url" in body["llm"]


def test_patch_assistant_llm_settings(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.patch(
        "/api/settings/assistant_llm",
        headers=headers,
        json={
            "data": {
                "enabled": True,
                "base_url": "https://api.example.com/v1",
                "model": "test-model",
                "api_key": "sk-test",
                "memory_enabled": False,
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["assistant_llm"]["enabled"] is True
    assert body["assistant_llm"]["model"] == "test-model"
    assert body["assistant_llm"]["api_key"] == "***"
    assert body["assistant_llm"]["memory_enabled"] is False


def test_assistant_config_merges_team_settings(settings_client):
    _client, _config, owner, team_id, sf = settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="assistant_llm",
        patch={
            "enabled": True,
            "base_url": "https://team.example/v1",
            "model": "team-model",
            "api_key": "sk-team",
            "memory_enabled": False,
        },
        member_id=owner.id,
    )
    session.commit()
    session.close()

    cfg = AssistantConfig(
        team_slug="test",
        llm=AssistantLlmConfig(enabled=False, model="env-model", api_key="sk-env"),
        memory_enabled=True,
    )
    from assistant_platform.config import _apply_team_assistant_llm_overrides

    merged = _apply_team_assistant_llm_overrides(cfg)
    assert merged.llm.enabled is True
    assert merged.llm.model == "team-model"
    assert merged.llm.base_url == "https://team.example/v1"
    assert merged.llm.api_key == "sk-team"
    assert merged.memory_enabled is False


def test_load_assistant_config_reads_team_settings(settings_client, monkeypatch):
    _client, _config, owner, team_id, sf = settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="assistant_llm",
        patch={"enabled": True, "model": "from-db", "api_key": "sk-db"},
        member_id=owner.id,
    )
    session.commit()
    session.close()

    monkeypatch.setenv("PULSE_TEAM_SLUG", "test")
    monkeypatch.delenv("ASSISTANT_LLM_MODEL", raising=False)
    monkeypatch.delenv("ASSISTANT_LLM_API_KEY", raising=False)

    cfg = load_assistant_config()
    assert cfg.llm.model == "from-db"
    assert cfg.llm.api_key == "sk-db"
    assert cfg.llm.enabled is True
