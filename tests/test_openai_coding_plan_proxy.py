"""M4 OpenAI Coding Plan gateway."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import pytest
from pulse.ingestion.crypto import encrypt_secret
from pulse.openai_proxy.authorize import authorize_pkcp
from pulse.openai_proxy.pool import list_cp_admin_accounts, list_cp_pool_entries, pick_cp_credential
from pulse.openai_proxy.upstream import coding_plan_gateway_public_base, cp_gateway_endpoints, openai_base_url
from pulse.openai_proxy.usage import parse_openai_usage
from pulse.proxy.key_crud import create_coding_plan_key
from pulse.storage.db import init_db
from pulse.storage.models import AiAccount, AiAccountCredential, AiPlan, AiVendor, Member, Team, TeamSetting
from pulse.tool_center.seed import seed_v2_catalog
from sqlalchemy import select
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_coding_plan_gateway_public_base():
    assert (
        coding_plan_gateway_public_base(proxy_public_url="http://127.0.0.1:8317") == "http://127.0.0.1:8317/openai/v1"
    )


def test_openai_base_url_glm_regions():
    assert openai_base_url(vendor_slug="glm", api_region="zai").startswith("https://api.z.ai/")
    assert "coding" in openai_base_url(vendor_slug="glm", api_region="bigmodel")
    assert openai_base_url(vendor_slug="minimax", api_region="cn").startswith("https://api.minimaxi.com/")


def test_pkcp_authorize(session):
    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m1", display_name="M1")
    session.add(member)
    seed_v2_catalog(session, team)
    session.flush()
    key, plain = create_coding_plan_key(
        session,
        name="glm pool",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    session.commit()
    ok = authorize_pkcp(session, plain)
    assert ok["status"] == "ok"
    assert ok["coding_plan_vendor"] == "glm"
    bad = authorize_pkcp(session, "pk_deadbeef")
    assert bad["status"] == "invalid"


def test_cp_pool_picks_enabled_account(session):
    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m2", display_name="M2")
    session.add(member)
    seed_v2_catalog(session, team)
    vendor = session.scalar(select(AiVendor).where(AiVendor.slug == "glm"))
    plan = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id))
    acc = AiAccount(
        team_id=team.id,
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="glm-pool@test",
        api_region="zai",
        cp_proxy_enabled=True,
    )
    session.add(acc)
    session.flush()
    session.add(
        AiAccountCredential(
            account_id=acc.id,
            vendor_id=vendor.id,
            credential_type="coding_plan_api_key",
            encrypted_value=encrypt_secret("glm-test-key", TEST_KEY),
            key_hint="glm…key",
            key_role="primary",
            bound_by_member_id=member.id,
            last_sync_status="success",
            last_sync_at=datetime.now(UTC),
        )
    )
    session.commit()
    entries = list_cp_pool_entries(session, vendor_slug="glm", encryption_key=TEST_KEY, include_api_keys=True)
    assert len(entries) == 1
    picked = pick_cp_credential(session, vendor_slug="glm", encryption_key=TEST_KEY)
    assert picked and picked["api_key"] == "glm-test-key"


def test_parse_openai_usage():
    body = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    t = parse_openai_usage(body)
    assert t["input"] == 10 and t["total"] == 15


def test_internal_openai_resolve():
    from fastapi.testclient import TestClient
    from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
    from pulse.web.app import create_app

    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    session_factory = init_db("sqlite:///:memory:")
    app = create_app(config, session_factory=session_factory)
    client = TestClient(app)
    session = session_factory()

    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m-int", display_name="MInt")
    session.add(member)
    seed_v2_catalog(session, team)
    vendor = session.scalar(select(AiVendor).where(AiVendor.slug == "glm"))
    plan = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id))
    acc = AiAccount(
        team_id=team.id,
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="glm-resolve@test",
        api_region="zai",
        cp_proxy_enabled=True,
    )
    session.add(acc)
    session.flush()
    session.add(
        AiAccountCredential(
            account_id=acc.id,
            vendor_id=vendor.id,
            credential_type="coding_plan_api_key",
            encrypted_value=encrypt_secret("resolve-key", TEST_KEY),
            key_hint="r…key",
            key_role="primary",
            bound_by_member_id=member.id,
            last_sync_status="success",
            last_sync_at=datetime.now(UTC),
        )
    )
    _key, plain = create_coding_plan_key(
        session,
        name="resolve",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    session.commit()

    bad = client.post(
        "/api/internal/v1/openai-proxy/resolve",
        json={"pulse_key": "pkcp_bad"},
        headers={"Authorization": "Bearer internal-token"},
    )
    assert bad.status_code == 200
    assert bad.json()["status"] == "invalid"

    ok = client.post(
        "/api/internal/v1/openai-proxy/resolve",
        json={"pulse_key": plain},
        headers={"Authorization": "Bearer internal-token"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "ok"
    assert body["api_key"] == "resolve-key"
    assert body["upstream_chat_url"].endswith("/chat/completions")
    session.close()


def test_cp_gateway_endpoints_uses_team_proxy_addresses(session):
    from pulse.config import AppConfig, ProxyConfig, TenantConfig

    team, _repo = make_team_repo(session)
    session.add(
        TeamSetting(
            team_id=team.id,
            section="proxy_addresses",
            data={
                "addresses": [
                    {"url": "http://192.168.11.39:8317", "display_name": "公司"},
                    {"url": "http://116.236.221.185:8317", "display_name": "外网"},
                ]
            },
        )
    )
    session.commit()
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="T"),
        proxy=ProxyConfig(public_url="http://127.0.0.1:8317"),
    )
    endpoints = cp_gateway_endpoints(session=session, config=config)
    assert len(endpoints) == 2
    assert endpoints[0]["display_name"] == "公司"
    assert endpoints[0]["openai_base_url"] == "http://192.168.11.39:8317/openai/v1"
    assert endpoints[1]["openai_base_url"] == "http://116.236.221.185:8317/openai/v1"


def test_cp_openai_endpoints_api():
    from fastapi.testclient import TestClient
    from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
    from pulse.web.app import create_app
    from pulse.web.auth_tokens import create_access_token
    from pulse.web.portal import bootstrap_portal_owner

    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    session_factory = init_db("sqlite:///:memory:")
    app = create_app(config, session_factory=session_factory)
    client = TestClient(app)
    session = session_factory()

    team, repo = make_team_repo(session)
    session.add(
        TeamSetting(
            team_id=team.id,
            section="proxy_addresses",
            data={"addresses": [{"url": "http://proxy.example.com:8317", "display_name": "示例"}]},
        )
    )
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(config, owner)}"}

    resp = client.get("/api/v2/openai-proxy/endpoints", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["endpoints"][0]["openai_base_url"] == "http://proxy.example.com:8317/openai/v1"
    session.close()


def test_create_cp_proxy_key_empty_remark_leaves_name_blank():
    from fastapi.testclient import TestClient
    from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
    from pulse.web.app import create_app

    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    session_factory = init_db("sqlite:///:memory:")
    app = create_app(config, session_factory=session_factory)
    client = TestClient(app)
    session = session_factory()

    team, repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m-empty", display_name="Empty")
    session.add(member)
    seed_v2_catalog(session, team)
    session.commit()

    from pulse.web.auth_tokens import create_access_token
    from pulse.web.portal import bootstrap_portal_owner

    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(config, owner)}"}

    resp = client.post(
        "/api/v2/openai-proxy/keys",
        headers=headers,
        json={
            "member_id": member.id,
            "coding_plan_vendor": "glm",
            "name": "   ",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == ""
    assert body["key_hint"].startswith("pkcp_")
    assert body["name"] != body["key_hint"]
    session.close()


def test_cp_key_usage_summary_and_usages_endpoint():
    from fastapi.testclient import TestClient
    from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
    from pulse.openai_proxy.usage import record_cp_gateway_usage
    from pulse.web.app import create_app

    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    session_factory = init_db("sqlite:///:memory:")
    app = create_app(config, session_factory=session_factory)
    client = TestClient(app)
    session = session_factory()

    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m-u", display_name="MU")
    session.add(member)
    seed_v2_catalog(session, team)
    key, _plain = create_coding_plan_key(
        session,
        name="usage-key",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    record_cp_gateway_usage(
        session,
        proxy_key_id=key.id,
        credential_id=None,
        model="glm-5.2",
        tokens={"input": 10, "output": 5, "total": 15},
    )
    session.commit()

    from pulse.web.auth_tokens import create_access_token
    from pulse.web.portal import bootstrap_portal_owner

    owner = bootstrap_portal_owner(_repo, channel_user_id="admin", display_name="Admin", password="x")
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(config, owner)}"}

    listed = client.get("/api/v2/openai-proxy/keys", headers=headers)
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["id"] == key.id)
    assert row["total_tokens"] == 15
    assert row["request_count"] == 1
    assert row["window_5h_tokens"] == 15

    detail = client.get(f"/api/v2/openai-proxy/keys/{key.id}/usages", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["summary"]["total_tokens"] == 15
    assert body["by_model"][0]["model"] == "glm-5.2"
    session.close()


def test_cp_sticky_reuses_credential_within_dwell(session):
    from pulse.config import AppConfig, LoanSelectionConfig, TenantConfig, ToolCenterConfig
    from pulse.openai_proxy.sticky_pick import resolve_cp_credential
    from pulse.storage.models import CpOpenAiStickyBinding

    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m-st", display_name="MSt")
    session.add(member)
    seed_v2_catalog(session, team)
    vendor = session.scalar(select(AiVendor).where(AiVendor.slug == "glm"))
    plan = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id))
    for ident in ("glm-a@test", "glm-b@test"):
        acc = AiAccount(
            team_id=team.id,
            vendor_id=vendor.id,
            plan_id=plan.id,
            account_identifier=ident,
            api_region="zai",
            cp_proxy_enabled=True,
        )
        session.add(acc)
        session.flush()
        session.add(
            AiAccountCredential(
                account_id=acc.id,
                vendor_id=vendor.id,
                credential_type="coding_plan_api_key",
                encrypted_value=encrypt_secret(f"key-{ident}", TEST_KEY),
                key_hint="k…",
                key_role="primary",
                bound_by_member_id=member.id,
                last_sync_status="success",
                last_sync_at=datetime.now(UTC),
            )
        )
    key, _plain = create_coding_plan_key(
        session,
        name="sticky",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    session.commit()
    config = AppConfig(
        tenant=TenantConfig(slug="t", name="T"),
        tool_center=ToolCenterConfig(loan_selection=LoanSelectionConfig(min_switch_minutes=30)),
    )
    first = resolve_cp_credential(
        session,
        proxy_key_id=key.id,
        vendor_slug="glm",
        encryption_key=TEST_KEY,
        config=config,
        team_id=team.id,
    )
    assert first and first["account_identifier"] == "glm-a@test"
    session.commit()
    second = resolve_cp_credential(
        session,
        proxy_key_id=key.id,
        vendor_slug="glm",
        encryption_key=TEST_KEY,
        config=config,
        team_id=team.id,
    )
    assert second and second["credential_id"] == first["credential_id"]
    binding = session.get(CpOpenAiStickyBinding, key.id)
    assert binding is not None


def test_cp_key_reveal_endpoint():
    from fastapi.testclient import TestClient

    from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
    from pulse.web.app import create_app
    from pulse.web.auth_tokens import create_access_token
    from pulse.web.portal import bootstrap_portal_owner

    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    session_factory = init_db("sqlite:///:memory:")
    app = create_app(config, session_factory=session_factory)
    client = TestClient(app)
    session = session_factory()

    team, repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m-rev2", display_name="MRev2")
    session.add(member)
    seed_v2_catalog(session, team)
    key, plain = create_coding_plan_key(
        session,
        name="reveal-me",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    session.commit()
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(config, owner)}"}

    resp = client.get(f"/api/v2/openai-proxy/keys/{key.id}/reveal", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["plaintext_key"] == plain
    assert "/openai/v1" in resp.json()["openai_base_url"]
    session.close()


def test_cp_key_revoke_endpoint():
    from fastapi.testclient import TestClient
    from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
    from pulse.openai_proxy.authorize import authorize_pkcp
    from pulse.web.app import create_app
    from pulse.web.auth_tokens import create_access_token
    from pulse.web.portal import bootstrap_portal_owner

    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    session_factory = init_db("sqlite:///:memory:")
    app = create_app(config, session_factory=session_factory)
    client = TestClient(app)
    session = session_factory()

    team, repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m-rev", display_name="MRev")
    session.add(member)
    seed_v2_catalog(session, team)
    _key, plain = create_coding_plan_key(
        session,
        name="revoke-me",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    session.commit()
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(config, owner)}"}

    assert authorize_pkcp(session, plain)["status"] == "ok"
    resp = client.post(f"/api/v2/openai-proxy/keys/{_key.id}/revoke", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"
    session.expire_all()
    assert authorize_pkcp(session, plain)["status"] == "invalid"
    session.close()


def test_cp_admin_accounts_list(session):
    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m3", display_name="M3")
    session.add(member)
    seed_v2_catalog(session, team)
    vendor = session.scalar(select(AiVendor).where(AiVendor.slug == "glm"))
    plan = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id))
    acc = AiAccount(
        team_id=team.id,
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="glm-admin@test",
        cp_proxy_enabled=False,
    )
    session.add(acc)
    session.commit()
    rows = list_cp_admin_accounts(session, vendor_slug="glm")
    assert any(r["account_identifier"] == "glm-admin@test" for r in rows)
    assert rows[0]["pool_ready"] is False
