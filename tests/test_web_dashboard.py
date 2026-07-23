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
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="a1", display_name="A", password="x")
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
    assert "cost_summary" in body
    assert "alert_summary" in body
    assert "summary" in body
    assert "pending_actions" in body
    assert body["pending_actions"]["total_count"] >= 0


def test_system_schedule(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/schedule", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["reminders_enabled"] is False
    job_ids = {job["id"] for job in body["jobs"]}
    assert "monthly_report" in job_ids
    assert "collection_start" not in job_ids


def test_system_schedule_memory_evolution_not_scheduled(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.patch(
        "/api/settings/memory",
        headers=headers,
        json={"data": {"evolution_day_of_week": -1, "evolution_time": "03:15"}},
    )
    assert res.status_code == 200

    res = client.get("/api/system/schedule", headers=headers)
    assert res.status_code == 200
    job_ids = {job["id"] for job in res.json()["jobs"]}
    assert "memory_evolution" not in job_ids


def test_system_integrations(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/integrations", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "dingtalk" in body
    assert "pulse_llm" in body
    assert "assistant_llm" in body
    assert "runtime_note" in body
    assert "object_storage" not in body
    assert "cursor_teams" not in body
