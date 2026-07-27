import pytest

pytest.importorskip("fastapi")
from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _dash_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def dash_client(_dash_app):
    client, config, proxy = _dash_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    _team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    repo.add_member("u1", "Bob")
    repo.commit()
    s.close()
    return client, config, owner


def test_dashboard_overview(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    h = {"Authorization": f"Bearer {token}"}
    res = client.get("/api/dashboard/overview", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert "ingestion" in body
    assert body["ingestion"]["active_count"] >= 0
    assert "submitted_count" in body["ingestion"]
    assert "sync_stats" in body
    assert "summary" in body
    assert "pending_actions" in body
    assert body["pending_actions"]["total_count"] >= 0


def test_system_schedule(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/schedule", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    job_ids = {job["id"] for job in body["jobs"]}
    assert "cursor_sync_tick" in job_ids
    assert "expire_key_loans" in job_ids
    assert "collection_start" not in job_ids
    assert "monthly_report" not in job_ids


def test_system_integrations(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/integrations", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "dingtalk" in body
    assert "feishu" in body
    assert "bot_platform" in body
    assert "im_group_configured" in body
    assert "assistant_llm" in body
    assert "runtime_note" in body
    assert "pulse_llm" not in body
