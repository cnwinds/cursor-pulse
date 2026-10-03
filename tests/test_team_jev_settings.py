"""Jev 团队设置的读写与生效链路（Auto Lender 主判）。"""

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.settings.team_store import effective_config_for_tenant
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from pulse.web.settings_store import patch_team_setting
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _jev_settings_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def settings_client(_jev_settings_app, tmp_path):
    client, config, proxy = _jev_settings_app
    db_url = f"sqlite:///{(tmp_path / 'pulse.db').as_posix()}"
    config.storage.database_url = db_url
    sf = make_test_session_factory(db_url)
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    repo.commit()
    s.close()
    return client, config, owner, team.id, sf


def _auth(config, owner) -> dict:
    return {"Authorization": f"Bearer {create_access_token(config, owner)}"}


def test_settings_exposes_jev_section(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    res = client.get("/api/settings", headers=_auth(config, owner))
    assert res.status_code == 200
    body = res.json()
    assert "jev" in body
    assert body["jev"]["model"] == "typesafe/jev-1.13"
    assert body["jev"]["enabled"] is False


def test_patch_jev_settings_masks_api_key(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    res = client.patch(
        "/api/settings/jev",
        headers=_auth(config, owner),
        json={
            "data": {
                "enabled": True,
                "base_url": "https://openrouter.ai/api",
                "model": "typesafe/jev-1.13",
                "api_key": "or-secret",
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["jev"]["enabled"] is True
    assert body["jev"]["model"] == "typesafe/jev-1.13"
    assert body["jev"]["api_key"] == "***"


def test_reveal_jev_api_key(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    client.patch(
        "/api/settings/jev",
        headers=_auth(config, owner),
        json={"data": {"enabled": True, "api_key": "or-secret"}},
    )
    res = client.get("/api/settings/jev/reveal/api_key", headers=_auth(config, owner))
    assert res.status_code == 200
    assert res.json()["value"] == "or-secret"


def test_team_jev_settings_reach_effective_config(settings_client):
    """Auto Lender 读的是 effective config，团队设置必须能覆盖 env 默认值。"""
    _client, _config, owner, team_id, sf = settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="jev",
        patch={
            "enabled": True,
            "base_url": "https://openrouter.ai/api",
            "model": "typesafe/jev-1.13",
            "api_key": "or-team",
        },
        member_id=owner.id,
    )
    session.commit()

    runtime = effective_config_for_tenant(session, AppConfig(tenant=TenantConfig(slug="test", name="Test")))
    session.close()

    assert runtime.jev.enabled is True
    assert runtime.jev.api_key == "or-team"
    assert runtime.jev.model == "typesafe/jev-1.13"

    from pulse.llm.jev import build_jev_client

    client = build_jev_client(runtime)
    assert client is not None
    assert client.decisions_url == "https://openrouter.ai/api/alpha/decisions"
