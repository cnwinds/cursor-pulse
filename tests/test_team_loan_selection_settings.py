"""选号规则可以按团队保存，并在读取设置时生效。"""

import pytest

pytest.importorskip("fastapi")

from pulse.settings.team_store import effective_loan_selection
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory
from pulse.config import AppConfig, TenantConfig, WebConfig


@pytest.fixture(scope="module")
def _loan_settings_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def settings_client(_loan_settings_app, tmp_path):
    client, config, proxy = _loan_settings_app
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


def test_settings_exposes_loan_selection(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    res = client.get("/api/settings", headers=_auth(config, owner))
    assert res.status_code == 200
    sel = res.json()["tool_center"]["loan_selection"]
    assert sel["max_concurrent_users"] == 3
    assert sel["concurrent_ttl_seconds"] == 180
    assert sel["min_switch_minutes"] == 30


def test_patch_loan_selection_updates_effective_config(settings_client):
    client, config, owner, team_id, sf = settings_client
    res = client.patch(
        "/api/settings/tool_center",
        headers=_auth(config, owner),
        json={"data": {"loan_selection": {"max_concurrent_users": 5, "owner_reserve_pct": 20}}},
    )
    assert res.status_code == 200
    body = res.json()["tool_center"]["loan_selection"]
    assert body["max_concurrent_users"] == 5
    assert body["owner_reserve_pct"] == 20
    assert body["min_switch_minutes"] == 30

    session = sf()
    sel = effective_loan_selection(session, config, team_id)
    session.close()
    assert sel.max_concurrent_users == 5
    assert sel.owner_reserve_pct == 20


def test_patch_loan_selection_rejects_over_cap(settings_client):
    client, config, owner, _team_id, _sf = settings_client
    res = client.patch(
        "/api/settings/tool_center",
        headers=_auth(config, owner),
        json={"data": {"loan_selection": {"max_concurrent_users": 101}}},
    )
    assert res.status_code == 400
    assert "选号规则" in res.json()["detail"]
