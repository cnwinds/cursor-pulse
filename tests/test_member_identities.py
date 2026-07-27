from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.identity.service import IdentityError, ensure_identity, merge_members, resolve_member
from pulse.storage.models import AiAccount, AiPlan, AiVendor, Member, MemberIdentity
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import SessionFactoryProxy, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _identity_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test", admin_password="bootstrap-secret"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    config.storage.database_url = "sqlite://"
    proxy = SessionFactoryProxy()
    client = TestClient(create_app(config, proxy))
    return client, config, proxy


@pytest.fixture
def identity_client(_identity_app):
    client, config, proxy = _identity_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="A", password="owner-pass")
    repo.commit()
    s.close()
    return client, config, owner, team.id, sf


def test_migrate_backfills_identities(identity_client):
    _client, _config, owner, _team_id, sf = identity_client
    session = sf()
    rows = list(
        session.scalars(
            select(MemberIdentity).where(MemberIdentity.member_id == owner.id)
        ).all()
    )
    session.close()
    assert any(r.channel == "web" and r.external_id == "admin" for r in rows)


def test_create_local_user_and_password_login(identity_client):
    client, config, owner, _team_id, _sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.post(
        "/api/portal/users",
        headers=headers,
        json={
            "username": "alice",
            "display_name": "Alice",
            "password": "alice-secret",
            "portal_role": "operator",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["channel"] == "web"
    assert any(i["channel"] == "web" and i["external_id"] == "alice" for i in body["identities"])

    login = client.post("/api/auth/login", json={"username": "alice", "password": "alice-secret"})
    assert login.status_code == 200
    assert login.json()["user"]["display_name"] == "Alice"


def test_link_dingtalk_keeps_same_member(identity_client):
    client, config, owner, team_id, sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/portal/users",
        headers=headers,
        json={
            "username": "bob",
            "display_name": "Bob",
            "password": "bob-secret",
            "portal_role": "operator",
        },
    ).json()
    member_id = created["id"]

    link = client.post(
        f"/api/portal/users/{member_id}/identities",
        headers=headers,
        json={"channel": "dingtalk", "external_id": "dt-bob-1", "merge": False},
    )
    assert link.status_code == 200
    identities = link.json()["identities"]
    assert { (i["channel"], i["external_id"]) for i in identities } >= {
        ("web", "bob"),
        ("dingtalk", "dt-bob-1"),
    }

    session = sf()
    resolved = resolve_member(session, team_id, channel="dingtalk", external_id="dt-bob-1")
    assert resolved is not None
    assert resolved.id == member_id
    session.close()


def test_delete_local_user_removes_identities(identity_client):
    client, config, owner, _team_id, sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/portal/users",
        headers=headers,
        json={
            "username": "todelete",
            "display_name": "ToDelete",
            "password": "delete-secret",
            "portal_role": "operator",
        },
    ).json()
    member_id = created["id"]
    res = client.delete(f"/api/portal/users/{member_id}", headers=headers)
    assert res.status_code == 200
    session = sf()
    assert session.get(Member, member_id) is None
    assert (
        session.scalars(
            select(MemberIdentity).where(MemberIdentity.member_id == member_id)
        ).first()
        is None
    )
    session.close()


def test_merge_members_rewrites_primary(identity_client):
    _client, _config, _owner, team_id, sf = identity_client
    session = sf()
    keep = Member(
        team_id=team_id,
        channel="web",
        channel_user_id="keep",
        display_name="Keep",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    drop = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="dt-drop",
        display_name="Drop",
        status="active",
    )
    session.add_all([keep, drop])
    session.flush()
    ensure_identity(session, keep, channel="web", external_id="keep")
    ensure_identity(session, drop, channel="dingtalk", external_id="dt-drop")
    merged = merge_members(session, keep=keep, drop=drop)
    session.commit()
    assert merged.id == keep.id
    assert resolve_member(session, team_id, channel="dingtalk", external_id="dt-drop").id == keep.id
    assert session.get(Member, drop.id) is None
    session.close()


def test_reserved_admin_username_rejected(identity_client):
    client, config, owner, _team_id, _sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.post(
        "/api/portal/users",
        headers=headers,
        json={
            "username": "Admin",
            "display_name": "Nope",
            "password": "secret1",
            "portal_role": "operator",
        },
    )
    assert res.status_code == 400
    assert "保留" in res.json()["detail"]


def test_set_password_then_web_login(identity_client):
    client, config, owner, team_id, sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    session = sf()
    im_user = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="dt-carol",
        display_name="Carol",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    session.add(im_user)
    session.flush()
    ensure_identity(session, im_user, channel="dingtalk", external_id="dt-carol")
    member_id = im_user.id
    session.commit()
    session.close()

    set_pw = client.put(
        f"/api/portal/users/{member_id}/password",
        headers=headers,
        json={"password": "carol-secret", "username": "carol"},
    )
    assert set_pw.status_code == 200
    identities = {(i["channel"], i["external_id"]) for i in set_pw.json()["identities"]}
    assert ("web", "carol") in identities
    assert ("dingtalk", "dt-carol") in identities

    login = client.post("/api/auth/login", json={"username": "carol", "password": "carol-secret"})
    assert login.status_code == 200
    assert login.json()["user"]["id"] == member_id


def test_oauth_resolve_hits_linked_identity(identity_client):
    """Simulates OAuth callback: resolve_member finds admin-linked dingtalk userid."""
    client, config, owner, team_id, sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/portal/users",
        headers=headers,
        json={
            "username": "dana",
            "display_name": "Dana",
            "password": "dana-secret",
            "portal_role": "operator",
        },
    ).json()
    member_id = created["id"]
    assert (
        client.post(
            f"/api/portal/users/{member_id}/identities",
            headers=headers,
            json={"channel": "dingtalk", "external_id": "dt-dana-oauth", "merge": False},
        ).status_code
        == 200
    )
    session = sf()
    from pulse.storage.repository import Repository

    repo = Repository(session, team_id)
    found = repo.get_or_create_member("dt-dana-oauth", "Dana From OAuth", channel="dingtalk")
    assert found.id == member_id
    session.close()


def test_merge_rejects_conflicting_ledger_primaries(identity_client):
    _client, _config, _owner, team_id, sf = identity_client
    session = sf()
    keep = Member(
        team_id=team_id,
        channel="web",
        channel_user_id="keep2",
        display_name="Keep2",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    drop = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="dt-drop2",
        display_name="Drop2",
        status="active",
        portal_status="active",
        portal_role="owner",
    )
    session.add_all([keep, drop])
    session.flush()
    ensure_identity(session, keep, channel="web", external_id="keep2")
    ensure_identity(session, drop, channel="dingtalk", external_id="dt-drop2")
    vendor = AiVendor(slug="cursor-id-test", name="Cursor")
    session.add(vendor)
    session.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        plan_name="Pro",
        slug="pro-id-test",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
    )
    session.add(plan)
    session.flush()
    session.add_all(
        [
            AiAccount(
                team_id=team_id,
                vendor_id=vendor.id,
                plan_id=plan.id,
                account_identifier="a@example.com",
                primary_member_id=keep.id,
            ),
            AiAccount(
                team_id=team_id,
                vendor_id=vendor.id,
                plan_id=plan.id,
                account_identifier="b@example.com",
                primary_member_id=drop.id,
            ),
        ]
    )
    session.flush()
    with pytest.raises(IdentityError, match="主使用人"):
        merge_members(session, keep=keep, drop=drop)
    session.close()


def test_merge_keeps_higher_portal_role(identity_client):
    _client, _config, _owner, team_id, sf = identity_client
    session = sf()
    keep = Member(
        team_id=team_id,
        channel="web",
        channel_user_id="keep3",
        display_name="Keep3",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    drop = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="dt-drop3",
        display_name="Drop3",
        status="active",
        portal_status="active",
        portal_role="owner",
    )
    session.add_all([keep, drop])
    session.flush()
    ensure_identity(session, keep, channel="web", external_id="keep3")
    ensure_identity(session, drop, channel="dingtalk", external_id="dt-drop3")
    # Actor is owner so promotion to owner is allowed.
    merged = merge_members(session, keep=keep, drop=drop, actor=_owner)
    session.commit()
    assert merged.portal_role == "owner"
    session.close()


def test_set_password_keeps_dingtalk_primary_cache(identity_client):
    client, config, owner, team_id, sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    session = sf()
    im_user = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="dt-keep-im",
        display_name="ImUser",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    session.add(im_user)
    session.flush()
    ensure_identity(session, im_user, channel="dingtalk", external_id="dt-keep-im")
    member_id = im_user.id
    session.commit()
    session.close()

    res = client.put(
        f"/api/portal/users/{member_id}/password",
        headers=headers,
        json={"password": "im-secret1", "username": "imuser"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["channel"] == "dingtalk"
    assert body["channel_user_id"] == "dt-keep-im"

    session = sf()
    from pulse.identity.service import external_id_for

    member = session.get(Member, member_id)
    assert external_id_for(session, member, "dingtalk") == "dt-keep-im"
    assert external_id_for(session, member, "web") == "imuser"
    session.close()


def test_merge_rewrites_ledger_primary(identity_client):
    _client, _config, owner, team_id, sf = identity_client
    session = sf()
    keep = Member(
        team_id=team_id,
        channel="web",
        channel_user_id="keep-ledger",
        display_name="KeepL",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    drop = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="dt-ledger",
        display_name="DropL",
        status="active",
    )
    session.add_all([keep, drop])
    session.flush()
    ensure_identity(session, keep, channel="web", external_id="keep-ledger")
    ensure_identity(session, drop, channel="dingtalk", external_id="dt-ledger")
    vendor = AiVendor(slug="cursor-ledger-test", name="Cursor")
    session.add(vendor)
    session.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        plan_name="Pro",
        slug="pro-ledger",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
    )
    session.add(plan)
    session.flush()
    account = AiAccount(
        team_id=team_id,
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="ledger@example.com",
        primary_member_id=drop.id,
    )
    session.add(account)
    session.flush()
    account_id = account.id
    drop_id = drop.id
    keep_id = keep.id
    merge_members(session, keep=keep, drop=drop, actor=owner)
    session.commit()
    session.expire_all()
    assert session.get(AiAccount, account_id).primary_member_id == keep_id
    assert session.get(Member, drop_id) is None
    session.close()


def test_cleanup_legacy_oauth_deletes_identities(identity_client):
    _client, _config, _owner, team_id, sf = identity_client
    session = sf()
    from pulse.storage.repository import Repository
    from pulse.web.portal import _cleanup_legacy_oauth_duplicates

    keep = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="enterprise-uid-1",
        display_name="SameName",
        status="active",
    )
    legacy = Member(
        team_id=team_id,
        channel="dingtalk",
        channel_user_id="openidLegacyABC",
        display_name="SameName",
        status="active",
    )
    session.add_all([keep, legacy])
    session.flush()
    ensure_identity(session, keep, channel="dingtalk", external_id="enterprise-uid-1")
    ensure_identity(session, legacy, channel="dingtalk", external_id=legacy.channel_user_id)
    legacy_id = legacy.id
    repo = Repository(session, team_id)
    _cleanup_legacy_oauth_duplicates(repo, display_name="SameName", keep_id=keep.id)
    session.commit()
    assert session.get(Member, legacy_id) is None
    assert (
        session.scalars(
            select(MemberIdentity).where(MemberIdentity.member_id == legacy_id)
        ).first()
        is None
    )
    session.close()


def test_link_reserved_admin_username_rejected(identity_client):
    client, config, owner, _team_id, _sf = identity_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/portal/users",
        headers=headers,
        json={
            "username": "eve",
            "display_name": "Eve",
            "password": "eve-secret",
            "portal_role": "operator",
        },
    ).json()
    res = client.post(
        f"/api/portal/users/{created['id']}/identities",
        headers=headers,
        json={"channel": "web", "external_id": "admin", "merge": False},
    )
    assert res.status_code == 400
    assert "保留" in res.json()["detail"]


def test_merge_rejects_dropping_actor(identity_client):
    _client, _config, owner, team_id, sf = identity_client
    session = sf()
    other = Member(
        team_id=team_id,
        channel="web",
        channel_user_id="other-keep",
        display_name="Other",
        status="active",
        portal_status="active",
        portal_role="operator",
    )
    session.add(other)
    session.flush()
    ensure_identity(session, other, channel="web", external_id="other-keep")
    with pytest.raises(IdentityError, match="当前登录"):
        merge_members(session, keep=other, drop=owner, actor=owner)
    session.close()
