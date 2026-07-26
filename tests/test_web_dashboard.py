import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")
from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def dash_client():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    _team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    repo.add_member("u1", "Bob")
    repo.commit()
    s.close()
    return TestClient(create_app(config, sf)), config, owner


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
