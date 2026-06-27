import pytest
from sqlalchemy import create_engine

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base, Member
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def client():
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
    _team, repo = make_team_repo(session)
    repo.add_member("u1", "Alice")
    owner = bootstrap_portal_owner(
        repo,
        dingtalk_user_id="admin1",
        display_name="Admin",
        password="pass1234",
    )
    repo.commit()
    session.close()
    app = create_app(config, session_factory)
    yield TestClient(app), config, owner, session_factory


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_open(client):
    test_client, _, _, _ = client
    res = test_client.get("/health")
    assert res.status_code == 200


def test_members_requires_auth(client):
    test_client, config, owner, _ = client
    assert test_client.get("/api/members").status_code == 401
    token = create_access_token(config, owner)
    res = test_client.get("/api/members", headers=_auth_headers(token))
    assert res.status_code == 200
    names = [m["display_name"] for m in res.json()]
    assert "Alice" in names


def test_legacy_admin_token(client):
    test_client, _, _, _ = client
    res = test_client.get("/api/members", headers=_auth_headers("secret-token"))
    assert res.status_code == 200


def test_password_login(client):
    test_client, _, _, _ = client
    res = test_client.post(
        "/api/auth/login",
        json={"dingtalk_user_id": "admin1", "password": "pass1234"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["portal_role"] == "owner"
    assert "access_token" in body


def test_auth_me(client):
    test_client, config, owner, _ = client
    token = create_access_token(config, owner)
    res = test_client.get("/api/auth/me", headers=_auth_headers(token))
    assert res.status_code == 200
    assert res.json()["dingtalk_user_id"] == "admin1"


def test_permission_denied_for_auditor(client):
    test_client, config, _, session_factory = client
    session = session_factory()
    team, _repo = make_team_repo(session)
    auditor = Member(
        team_id=team.id,
        dingtalk_user_id="auditor1",
        display_name="Auditor",
        status="active",
        portal_role="auditor",
    )
    session.add(auditor)
    session.commit()
    token = create_access_token(config, auditor)
    session.close()

    assert test_client.get("/api/settings", headers=_auth_headers(token)).status_code == 200
    res = test_client.patch(
        "/api/settings/collection",
        headers=_auth_headers(token),
        json={"data": {"deadline_day": 5}},
    )
    assert res.status_code == 403
