import pytest
from sqlalchemy import create_engine

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def memory_client():
    config = AppConfig(
        web=WebConfig(admin_token="secret-token", jwt_secret="jwt-test-secret"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = session_factory()
    team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    member.portal_status = "pending"
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin1", display_name="Admin", password="pass1234"
    )
    repo.commit()
    session.close()
    app = create_app(config, session_factory)
    yield TestClient(app), config, owner, team.id


def test_memory_admin_endpoints_removed(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/memory/atoms", headers=headers).status_code == 404
    assert client.get("/api/memory/commitments", headers=headers).status_code == 404
    assert client.get("/api/memory/principles", headers=headers).status_code == 404
    assert client.post(
        "/api/memory/principles",
        headers=headers,
        json={"rule": "x", "tier": "learned"},
    ).status_code == 404
    assert client.get("/api/memory/disclosure", headers=headers).status_code == 404
    assert client.get("/api/memory/evolution", headers=headers).status_code == 404
    assert client.post("/api/memory/evolution/run", headers=headers).status_code == 404


def test_audit_logs(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    res = client.get("/api/audit-logs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "admin_actions" in body
    assert "alerts" in body


def test_portal_grant(memory_client):
    client, config, owner, _team_id = memory_client
    token = create_access_token(config, owner)
    pending = client.get(
        "/api/portal/users/pending",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    alice = next(u for u in pending if u["display_name"] == "Alice")
    res = client.post(
        f"/api/portal/users/{alice['id']}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"portal_role": "auditor"},
    )
    assert res.status_code == 200
    assert res.json()["portal_role"] == "auditor"
