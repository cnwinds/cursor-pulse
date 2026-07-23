import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base, Member
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.permissions import can_access_portal
from pulse.web.portal import (
    PortalAdminError,
    approve_portal_user,
    bootstrap_portal_owner,
    list_directory_portal_candidates,
    list_pending_portal_users,
    reconcile_oauth_member,
    reject_portal_user,
)
from tests.conftest import make_team_repo

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


@pytest.fixture
def portal_env():
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
    owner = bootstrap_portal_owner(
        repo,
        dingtalk_user_id="admin1",
        display_name="Super Admin",
        password="pass1234",
    )
    repo.commit()
    session.close()
    app = create_app(config, session_factory)
    client = TestClient(app)
    return client, config, owner, session_factory, team.id


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_first_dingtalk_login_enters_pending(portal_env):
    client, _, _, session_factory, _ = portal_env
    session = session_factory()
    _team, repo = make_team_repo(session)
    pending = repo.get_or_create_member("new_user", "New User")
    pending.portal_status = "pending"
    repo.commit()
    session.close()

    assert pending.portal_status == "pending"
    assert not can_access_portal(pending)


def test_approve_pending_user(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()
    pending = Member(
        team_id=team_id,
        dingtalk_user_id="pending1",
        display_name="Pending User",
        status="pending",
        portal_status="pending",
    )
    session.add(pending)
    session.commit()
    member_id = pending.id
    session.close()

    session = session_factory()
    approved = approve_portal_user(session, team_id, member_id, role="operator")
    session.commit()
    session.close()

    assert approved.portal_status == "active"
    assert approved.portal_role == "operator"
    assert approved.status == "active"
    assert can_access_portal(approved)


def test_reject_pending_user(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()
    pending = Member(
        team_id=team_id,
        dingtalk_user_id="reject1",
        display_name="Reject Me",
        status="pending",
        portal_status="pending",
    )
    session.add(pending)
    session.commit()
    member_id = pending.id
    session.close()

    session = session_factory()
    rejected = reject_portal_user(session, team_id, member_id)
    session.commit()
    session.close()
    assert rejected.portal_status == "rejected"
    assert rejected.portal_role is None


def test_portal_pending_api(portal_env):
    client, config, owner, session_factory, team_id = portal_env
    session = session_factory()
    pending = Member(
        team_id=team_id,
        dingtalk_user_id="api_pending",
        display_name="API Pending",
        status="pending",
        portal_status="pending",
    )
    session.add(pending)
    session.commit()
    pending_id = pending.id
    session.close()

    token = create_access_token(config, owner)
    res = client.get("/api/portal/users/pending", headers=_auth(token))
    assert res.status_code == 200
    ids = [u["id"] for u in res.json()]
    assert pending_id in ids


def test_portal_approve_api(portal_env):
    client, config, owner, session_factory, team_id = portal_env
    session = session_factory()
    pending = Member(
        team_id=team_id,
        dingtalk_user_id="approve_api",
        display_name="Approve API",
        status="pending",
        portal_status="pending",
    )
    session.add(pending)
    session.commit()
    pending_id = pending.id
    session.close()

    token = create_access_token(config, owner)
    res = client.post(
        f"/api/portal/users/{pending_id}/approve",
        headers=_auth(token),
        json={"portal_role": "auditor"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["portal_status"] == "active"
    assert body["portal_role"] == "auditor"


def test_cannot_approve_non_pending(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()
    active = Member(
        team_id=team_id,
        dingtalk_user_id="already_active",
        display_name="Active",
        status="active",
        portal_status="active",
        portal_role="auditor",
    )
    session.add(active)
    session.commit()
    member_id = active.id
    session.close()

    session = session_factory()
    updated = approve_portal_user(session, team_id, member_id, role="operator")
    session.commit()
    session.close()
    assert updated.portal_role == "operator"
    assert updated.portal_status == "active"


def test_list_pending_portal_users(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()
    for i in range(2):
        session.add(
            Member(
                team_id=team_id,
                dingtalk_user_id=f"p{i}",
                display_name=f"P{i}",
                status="pending",
                portal_status="pending",
            )
        )
    session.commit()
    pending = list_pending_portal_users(session, team_id)
    assert len(pending) == 2
    session.close()


def test_list_directory_portal_candidates(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()

    session.add(
        Member(
            team_id=team_id,
            dingtalk_user_id="dir1",
            display_name="通讯录用户",
            status="pending",
            portal_status=None,
            department_name="研发部",
        )
    )
    session.add(
        Member(
            team_id=team_id,
            dingtalk_user_id="active1",
            display_name="已开通",
            status="active",
            portal_status="active",
            portal_role="ai_member",
            department_name="研发部",
        )
    )
    session.commit()

    candidates = list_directory_portal_candidates(session, team_id)
    assert len(candidates) == 1
    assert candidates[0].dingtalk_user_id == "dir1"

    approved = approve_portal_user(session, team_id, candidates[0].id, role="ai_member")
    session.commit()
    assert approved.portal_status == "active"
    assert approved.portal_role == "ai_member"
    session.close()


def test_search_local_directory_members(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()
    from pulse.web.portal import search_local_directory_members

    session.add(
        Member(
            team_id=team_id,
            dingtalk_user_id="local1",
            display_name="周其清",
            status="pending",
            portal_status=None,
            department_name="研发部",
        )
    )
    session.commit()

    found = search_local_directory_members(session, team_id, "周其")
    assert len(found) == 1
    assert found[0].display_name == "周其清"
    session.close()


def test_portal_directory_search_api_local(portal_env):
    client, config, owner, session_factory, team_id = portal_env
    session = session_factory()
    session.add(
        Member(
            team_id=team_id,
            dingtalk_user_id="local_api",
            display_name="周其清",
            status="pending",
            portal_status=None,
            department_name="研发部",
        )
    )
    session.commit()
    session.close()

    token = create_access_token(config, owner)
    res = client.get(
        "/api/portal/users/directory-search",
        params={"q": "周其清"},
        headers=_auth(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["display_name"] == "周其清"


def test_portal_directory_search_api(portal_env, monkeypatch):
    client, config, owner, session_factory, team_id = portal_env

    class FakeClient:
        def get_department(self, dept_id: int):
            return {"name": "研发部"}

        def list_sub_departments(self, dept_id: int):
            return []

        def list_users_in_dept(self, dept_id: int):
            return [{"userid": "search_u1", "name": "搜索用户"}]

    monkeypatch.setattr(
        "pulse.integrations.dingtalk_directory.make_directory_client",
        lambda _config: FakeClient(),
    )

    token = create_access_token(config, owner)
    res = client.get(
        "/api/portal/users/directory-search",
        params={"q": "搜索"},
        headers=_auth(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["display_name"] == "搜索用户"
    assert body[0]["portal_status"] is None


def test_portal_directory_candidates_api(portal_env):
    client, config, owner, session_factory, team_id = portal_env
    session = session_factory()
    session.add(
        Member(
            team_id=team_id,
            dingtalk_user_id="api_dir",
            display_name="API 用户",
            status="pending",
            portal_status=None,
            department_name="产品部",
        )
    )
    session.commit()
    session.close()

    token = create_access_token(config, owner)
    res = client.get("/api/portal/users/directory-candidates", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["display_name"] == "API 用户"


def test_reconcile_oauth_member_migrates_openid_and_cleans_duplicate(portal_env):
    _, _, _, session_factory, team_id = portal_env
    session = session_factory()
    _team, repo = make_team_repo(session)
    enterprise = Member(
        team_id=team_id,
        dingtalk_user_id="1584929783723323",
        display_name="熊波",
        status="active",
        portal_status="active",
        portal_role="owner",
    )
    legacy = Member(
        team_id=team_id,
        dingtalk_user_id="q1kd0KjUKjamrEbcOqeGjQiEiE",
        display_name="熊波",
        status="active",
        portal_status="active",
        portal_role="owner",
    )
    session.add_all([enterprise, legacy])
    session.commit()

    member = reconcile_oauth_member(
        repo,
        enterprise_userid="1584929783723323",
        display_name="熊波",
    )
    session.commit()

    assert member is not None
    assert member.id == enterprise.id
    remaining = session.scalars(
        select(Member).where(Member.team_id == team_id, Member.display_name == "熊波")
    ).all()
    assert len(remaining) == 1
    assert remaining[0].dingtalk_user_id == "1584929783723323"
    session.close()
