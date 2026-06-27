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
    assert body["submission"]["active_count"] >= 1
    assert "summary" in body


def test_system_schedule(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/schedule", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert len(res.json()["jobs"]) >= 4


def test_system_integrations(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/integrations", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "dingtalk" in res.json()
