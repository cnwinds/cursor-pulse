import pytest

fastapi = pytest.importorskip("fastapi")

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Member
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _admin_app():
    config = AppConfig(
        web=WebConfig(
            admin_token="secret-token",
            admin_password="pass1234",
            jwt_secret="jwt-test-secret",
        ),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def client(_admin_app):
    test_client, config, proxy = _admin_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    session = sf()
    _team, repo = make_team_repo(session)
    repo.add_member("u1", "Alice")
    owner = bootstrap_portal_owner(
        repo,
        channel_user_id="admin1",
        display_name="Admin",
        password="pass1234",
    )
    repo.commit()
    session.close()
    yield test_client, config, owner, sf


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_open(client):
    test_client, _, _, _ = client
    res = test_client.get("/health")
    assert res.status_code == 200


def test_legacy_admin_token(client):
    test_client, _, _, _ = client
    res = test_client.get("/api/settings", headers=_auth_headers("secret-token"))
    assert res.status_code == 200


def test_password_login(client):
    test_client, _, _, _ = client
    res = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "pass1234"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["portal_role"] == "owner"
    assert body["user"]["channel_user_id"] == "admin"
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["expires_in"] == 30 * 60


def test_refresh_and_logout_flow(client):
    test_client, _, _, _ = client
    login = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "pass1234"},
    )
    assert login.status_code == 200
    body = login.json()
    refresh = body["refresh_token"]
    access = body["access_token"]
    assert refresh != access
    assert not str(refresh).startswith("eyJ")

    me = test_client.get("/api/auth/me", headers=_auth_headers(access))
    assert me.status_code == 200

    refreshed = test_client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 200
    new_body = refreshed.json()
    assert new_body["refresh_token"] != refresh
    assert not str(new_body["refresh_token"]).startswith("eyJ")
    assert new_body["access_token"]
    assert "user" in new_body
    me2 = test_client.get("/api/auth/me", headers=_auth_headers(new_body["access_token"]))
    assert me2.status_code == 200

    # Rotated refresh works once more.
    ok = test_client.post(
        "/api/auth/refresh", json={"refresh_token": new_body["refresh_token"]}
    )
    assert ok.status_code == 200
    latest = ok.json()["refresh_token"]

    # Reuse of a prior refresh is rejected without killing the latest session.
    reuse = test_client.post(
        "/api/auth/refresh", json={"refresh_token": new_body["refresh_token"]}
    )
    assert reuse.status_code == 401
    still = test_client.post("/api/auth/refresh", json={"refresh_token": latest})
    assert still.status_code == 200
    latest = still.json()["refresh_token"]

    logout = test_client.post("/api/auth/logout", json={"refresh_token": latest})
    assert logout.status_code == 204

    after = test_client.post("/api/auth/refresh", json={"refresh_token": latest})
    assert after.status_code == 401


def test_refresh_rejects_unknown_token(client):
    test_client, _, _, _ = client
    res = test_client.post(
        "/api/auth/refresh", json={"refresh_token": "not-a-real-refresh-token"}
    )
    assert res.status_code == 401


def _password_login_client(admin_password: str):
    config = AppConfig(
        web=WebConfig(
            admin_token="secret-token",
            admin_password=admin_password,
            jwt_secret="jwt-test-secret",
        ),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    sf = make_test_session_factory()
    proxy.bind(sf)
    session = sf()
    _team, repo = make_team_repo(session)
    repo.add_member("u1", "Alice")
    bootstrap_portal_owner(
        repo,
        channel_user_id="admin1",
        display_name="Admin",
        password="pass1234",
    )
    repo.commit()
    session.close()
    return client, config


def test_password_login_accepts_hashed_admin_password():
    from pulse.web.passwords import hash_password

    test_client, _ = _password_login_client(hash_password("pass1234"))
    res = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "pass1234"},
    )
    assert res.status_code == 200
    assert res.json()["user"]["portal_role"] == "owner"


def test_password_login_rejects_wrong_password_with_hashed_admin_password():
    from pulse.web.passwords import hash_password

    test_client, _ = _password_login_client(hash_password("pass1234"))
    res = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert res.status_code == 401


def test_password_login_rejects_wrong_username(client):
    test_client, _, _, _ = client
    res = test_client.post(
        "/api/auth/login",
        json={"username": "other", "password": "pass1234"},
    )
    assert res.status_code == 401


def test_auth_me(client):
    test_client, config, owner, _ = client
    token = create_access_token(config, owner)
    res = test_client.get("/api/auth/me", headers=_auth_headers(token))
    assert res.status_code == 200
    assert res.json()["channel_user_id"] == "admin1"


def test_permission_denied_for_auditor(client):
    test_client, config, _, session_factory = client
    session = session_factory()
    team, _repo = make_team_repo(session)
    auditor = Member(
        team_id=team.id,
        channel_user_id="auditor1",
        display_name="Auditor",
        status="active",
        portal_status="active",
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
