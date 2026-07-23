from __future__ import annotations

import base64
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, ProxyConfig, TenantConfig, WebConfig
from pulse.ingestion.crypto import encrypt_secret
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    AiAccountCredential,
    AiPlan,
    AiVendor,
    Base,
    Member,
    ProxyKey,
)
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        proxy=ProxyConfig(public_url="http://proxy.example.com:8317"),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin", display_name="Admin", password="x"
    )
    vendor = AiVendor(slug="cursor", name="Cursor")
    s.add(vendor)
    s.flush()
    plan = AiPlan(
        vendor_id=vendor.id, plan_name="Pro", slug="pro",
        billing_type="subscription", price_amount=20, price_currency="USD",
    )
    s.add(plan)
    s.flush()
    account = AiAccount(
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="acct-1",
        team_id=team.id,
        primary_member_id=owner.id,
    )
    s.add(account)
    s.flush()
    cred = AiAccountCredential(
        account_id=account.id, vendor_id=vendor.id, credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-1", TEST_KEY), key_hint="cur...y-1",
        bound_by_member_id=owner.id,
    )
    s.add(cred)
    s.commit()
    s.close()
    return {
        "client": TestClient(create_app(config, sf)),
        "config": config,
        "owner": owner,
        "cred_id": cred.id,
        "account_id": account.id,
        "sf": sf,
    }


def _admin(env) -> dict:
    return {"Authorization": f"Bearer {create_access_token(env['config'], env['owner'])}"}


def _create_key(env, **extra):
    body = {"member_id": env["owner"].id, "mode": "unlimited", **extra}
    return env["client"].post("/api/v2/proxy-keys", json=body, headers=_admin(env))


def test_create_and_list_proxy_key(env):
    resp = _create_key(env, mode="quota", token_limit=1000000)
    assert resp.status_code == 200
    body = resp.json()
    assert body["plaintext_key"].startswith("pk_")
    assert body["mode"] == "quota"
    assert body["name"] == "Admin"
    assert body["recoverable"] is True
    assert body["proxy_url"] == "http://proxy.example.com:8317"

    resp = env["client"].get("/api/v2/proxy-keys", headers=_admin(env))
    keys = resp.json()
    assert len(keys) == 1
    assert keys[0]["name"] == "Admin"
    assert keys[0]["member_name"] == "Admin"
    assert keys[0]["total_tokens"] == 0
    assert keys[0]["recoverable"] is True
    assert "plaintext_key" not in keys[0]
    assert "key_hash" not in keys[0]


def test_create_quota_key_requires_no_limits_is_allowed(env):
    resp = _create_key(env, mode="quota")
    assert resp.status_code == 200


def test_update_revoke_resume_flow(env):
    client = env["client"]
    key_id = _create_key(env, mode="quota", token_limit=10).json()["id"]

    resp = client.patch(
        f"/api/v2/proxy-keys/{key_id}", json={"token_limit": 20, "name": "k2"},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "k2"
    assert resp.json()["token_limit"] == 20

    resp = client.post(f"/api/v2/proxy-keys/{key_id}/revoke", headers=_admin(env))
    assert resp.json()["status"] == "revoked"

    resp = client.post(f"/api/v2/proxy-keys/{key_id}/resume", headers=_admin(env))
    assert resp.status_code == 409


def test_pool_accounts_toggle(env):
    client = env["client"]
    resp = client.get("/api/v2/proxy-pool/accounts", headers=_admin(env))
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == env["account_id"]
    assert rows[0]["account_identifier"] == "acct-1"
    assert rows[0]["plan_name"] == "Pro"
    assert rows[0]["active_credential_count"] == 1
    assert rows[0]["proxy_enabled"] is False

    resp = client.post(
        f"/api/v2/proxy-pool/accounts/{env['account_id']}",
        json={"proxy_enabled": True},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    assert resp.json()["proxy_enabled"] is True

    resp = client.get("/api/v2/proxy-pool/credentials", headers=_admin(env))
    assert resp.json()[0]["proxy_enabled"] is True


def test_pool_accounts_count_excludes_loan_keys(env):
    s = env["sf"]()
    account = s.get(AiAccount, env["account_id"])
    s.add(
        AiAccountCredential(
            account_id=env["account_id"],
            vendor_id=account.vendor_id,
            credential_type="api_key",
            encrypted_value=encrypt_secret("loan-key", TEST_KEY),
            key_hint="loan...",
            key_role="loan",
            status="active",
            bound_by_member_id=env["owner"].id,
        )
    )
    s.commit()
    s.close()

    resp = env["client"].get("/api/v2/proxy-pool/accounts", headers=_admin(env))
    assert resp.status_code == 200
    assert resp.json()[0]["active_credential_count"] == 1


def test_pool_ranking_board(env):
    from datetime import date, datetime, timedelta, timezone

    today = date.today()
    now = datetime.now(timezone.utc)
    s = env["sf"]()
    account = s.get(AiAccount, env["account_id"])
    account.proxy_enabled = True
    vendor_id = account.vendor_id
    plan_id = account.plan_id
    team_id = account.team_id

    soon = AiAccount(
        vendor_id=vendor_id,
        plan_id=plan_id,
        account_identifier="acct-soon",
        team_id=team_id,
        proxy_enabled=True,
    )
    exhausted = AiAccount(
        vendor_id=vendor_id,
        plan_id=plan_id,
        account_identifier="acct-exhausted",
        team_id=team_id,
        proxy_enabled=True,
    )
    no_snap = AiAccount(
        vendor_id=vendor_id,
        plan_id=plan_id,
        account_identifier="acct-nosnap",
        team_id=team_id,
        proxy_enabled=True,
    )
    s.add_all([soon, exhausted, no_snap])
    s.flush()
    for acc, hint, key in [
        (soon, "soon...", "cursor-key-soon"),
        (exhausted, "ex...", "cursor-key-ex"),
        (no_snap, "ns...", "cursor-key-nosnap"),
    ]:
        s.add(
            AiAccountCredential(
                account_id=acc.id,
                vendor_id=vendor_id,
                credential_type="api_key",
                encrypted_value=encrypt_secret(key, TEST_KEY),
                key_hint=hint,
                key_role="primary",
                status="active",
                bound_by_member_id=env["owner"].id,
            )
        )
    s.add(
        AccountQuotaSnapshot(
            account_id=env["account_id"],
            captured_at=now,
            cycle_start=today - timedelta(days=5),
            cycle_end=today + timedelta(days=25),
            limit_cents=20000,
            used_cents=1000,
            remaining_cents=19000,
            total_pct=5.0,
        )
    )
    s.add(
        AccountQuotaSnapshot(
            account_id=soon.id,
            captured_at=now,
            cycle_start=today - timedelta(days=5),
            cycle_end=today + timedelta(days=2),
            limit_cents=7000,
            used_cents=500,
            remaining_cents=6500,
            total_pct=7.0,
        )
    )
    s.add(
        AccountQuotaSnapshot(
            account_id=exhausted.id,
            captured_at=now,
            cycle_start=today - timedelta(days=20),
            cycle_end=today + timedelta(days=10),
            limit_cents=7000,
            used_cents=7000,
            remaining_cents=0,
            total_pct=100.0,
        )
    )
    s.commit()
    s.close()

    resp = env["client"].get("/api/v2/proxy-pool/ranking", headers=_admin(env))
    assert resp.status_code == 200
    body = resp.json()
    assert "api_key" not in str(body)
    ranked_ids = [r["account_identifier"] for r in body["ranked"]]
    assert ranked_ids[0] == "acct-soon"
    assert "acct-1" in ranked_ids
    assert "acct-exhausted" not in ranked_ids
    assert "acct-nosnap" not in ranked_ids
    reasons = {e["account_identifier"]: e["reason"] for e in body["excluded"]}
    assert reasons["acct-exhausted"] == "exhausted"
    assert reasons["acct-nosnap"] == "no_snapshot"
    assert all("score" in r for r in body["ranked"])
    assert "loan_cap" not in reasons.values()


def test_pool_ranking_ignores_loan_cap(env):
    from datetime import date, datetime, timedelta, timezone

    from pulse.storage.models import KeyLoan

    today = date.today()
    now = datetime.now(timezone.utc)
    s = env["sf"]()
    account = s.get(AiAccount, env["account_id"])
    account.proxy_enabled = True
    s.add(
        AccountQuotaSnapshot(
            account_id=env["account_id"],
            captured_at=now,
            cycle_start=today - timedelta(days=5),
            cycle_end=today + timedelta(days=25),
            limit_cents=20000,
            used_cents=1000,
            remaining_cents=19000,
            total_pct=5.0,
        )
    )
    # 造 3 条 active loan，远超默认 max_active_loans_per_account=2
    for i in range(3):
        cred = AiAccountCredential(
            account_id=env["account_id"],
            vendor_id=account.vendor_id,
            credential_type="api_key",
            encrypted_value=encrypt_secret(f"loan-key-{i}", TEST_KEY),
            key_hint=f"loan{i}",
            key_role="loan",
            status="active",
            bound_by_member_id=env["owner"].id,
        )
        s.add(cred)
        s.flush()
        s.add(
            KeyLoan(
                source_account_id=env["account_id"],
                credential_id=cred.id,
                status="active",
            )
        )
    s.commit()
    s.close()

    resp = env["client"].get("/api/v2/proxy-pool/ranking", headers=_admin(env))
    assert resp.status_code == 200
    body = resp.json()
    ranked_ids = [r["account_identifier"] for r in body["ranked"]]
    assert "acct-1" in ranked_ids
    assert all(e["reason"] != "loan_cap" for e in body["excluded"])
    row = next(r for r in body["ranked"] if r["account_identifier"] == "acct-1")
    assert row["active_loans"] == 3


def test_usages_endpoint(env):
    key_id = _create_key(env).json()["id"]
    resp = env["client"].get(f"/api/v2/proxy-keys/{key_id}/usages", headers=_admin(env))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"by_account": [], "by_model": [], "items": []}


def test_usages_grouped_by_account(env):
    from pulse.storage.models import ProxyKeyUsage

    key_id = _create_key(env).json()["id"]
    s = env["sf"]()
    s.add_all(
        [
            ProxyKeyUsage(
                proxy_key_id=key_id,
                credential_id=env["cred_id"],
                model="claude-a",
                total_tokens=100,
                cost_cents=10,
            ),
            ProxyKeyUsage(
                proxy_key_id=key_id,
                credential_id=env["cred_id"],
                model="claude-b",
                total_tokens=50,
                cost_cents=5,
            ),
            ProxyKeyUsage(
                proxy_key_id=key_id,
                credential_id=None,
                model="unknown",
                total_tokens=7,
                cost_cents=1,
            ),
        ]
    )
    s.commit()
    s.close()

    resp = env["client"].get(f"/api/v2/proxy-keys/{key_id}/usages", headers=_admin(env))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["by_account"]) == 2
    top = body["by_account"][0]
    assert top["account_identifier"] == "acct-1"
    assert top["primary_member_name"] == "Admin"
    assert top["plan_name"] == "Pro"
    assert top["request_count"] == 2
    assert top["total_tokens"] == 150
    assert top["cost_cents"] == 15
    assert body["by_account"][1]["account_identifier"] == "未知账号"
    assert body["by_account"][1]["primary_member_name"] is None
    assert len(body["items"]) == 3
    matched = next(i for i in body["items"] if i["account_identifier"] == "acct-1")
    assert matched["primary_member_name"] == "Admin"
    assert any(i["account_identifier"] is None and i["primary_member_name"] is None for i in body["items"])
    assert body["by_model"] == [
        {"model": "claude-a", "request_count": 1, "total_tokens": 100, "cost_cents": 10},
        {"model": "claude-b", "request_count": 1, "total_tokens": 50, "cost_cents": 5},
        {"model": "unknown", "request_count": 1, "total_tokens": 7, "cost_cents": 1},
    ]


def test_usages_by_model_aggregates_all_rows(env):
    from pulse.storage.models import ProxyKeyUsage

    key_id = _create_key(env).json()["id"]
    s = env["sf"]()
    for model, tokens, cost in [
        ("claude-opus-4-8", 1000, 80),
        ("claude-opus-4-8", 500, 40),
        ("composer-2.5-fast", 200, 10),
        (None, 50, 5),
        ("", 25, 2),
    ]:
        s.add(
            ProxyKeyUsage(
                proxy_key_id=key_id,
                credential_id=env["cred_id"],
                model=model,
                total_tokens=tokens,
                cost_cents=cost,
            )
        )
    s.commit()
    s.close()

    resp = env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/usages",
        params={"limit": 2},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["by_model"] == [
        {
            "model": "claude-opus-4-8",
            "request_count": 2,
            "total_tokens": 1500,
            "cost_cents": 120,
        },
        {
            "model": "composer-2.5-fast",
            "request_count": 1,
            "total_tokens": 200,
            "cost_cents": 10,
        },
        {
            "model": "（未知）",
            "request_count": 2,
            "total_tokens": 75,
            "cost_cents": 7,
        },
    ]


def test_requires_permission(env):
    resp = env["client"].get("/api/v2/proxy-keys")
    assert resp.status_code == 401


def _member_headers(env, permissions: list[str], *, display_name: str = "Aud"):
    s = env["sf"]()
    member = Member(
        id=str(uuid.uuid4()),
        team_id=env["owner"].team_id,
        dingtalk_user_id=f"u-{uuid.uuid4().hex[:8]}",
        display_name=display_name,
        status="active",
        portal_status="active",
        portal_role="custom",
        portal_permissions=permissions,
    )
    s.add(member)
    s.commit()
    token = create_access_token(env["config"], member)
    member_id = member.id
    s.close()
    return {"Authorization": f"Bearer {token}"}, member_id


def test_auditor_read_only(env):
    headers, _ = _member_headers(env, ["proxy:read"])
    assert env["client"].get("/api/v2/proxy-keys", headers=headers).status_code == 200
    resp = env["client"].post(
        "/api/v2/proxy-keys",
        json={"member_id": env["owner"].id, "mode": "unlimited"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_client_setup_admin_and_owner(env):
    headers, member_id = _member_headers(env, ["proxy:read"], display_name="借款人")
    # 管理员为该成员创建 key
    created = env["client"].post(
        "/api/v2/proxy-keys",
        json={"member_id": member_id, "mode": "unlimited"},
        headers=_admin(env),
    ).json()
    key_id = created["id"]
    plaintext = created["plaintext_key"]

    # 管理员可复制命令
    resp = env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        params={"shell": "powershell"},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plaintext_key"] == plaintext
    assert body["proxy_url"] == "http://proxy.example.com:8317"
    assert "$env:HTTPS_PROXY" in body["command"]
    assert plaintext in body["command"]
    assert "agent -k" in body["command"]
    assert "cursor-agent" not in body["command"]

    # 本人可读
    resp = env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        params={"shell": "bash"},
        headers=headers,
    )
    assert resp.status_code == 200
    bash_cmd = resp.json()["command"]
    assert "export HTTPS_PROXY" in bash_cmd
    assert "agent -k" in bash_cmd
    assert "cursor-agent" not in bash_cmd

    # 其他只读用户不可读他人 key
    other_headers, _ = _member_headers(env, ["proxy:read"], display_name="路人")
    resp = env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        headers=other_headers,
    )
    assert resp.status_code == 403


def test_client_setup_legacy_unrecoverable(env):
    s = env["sf"]()
    key = ProxyKey(
        key_hash="a" * 64,
        key_hint="pk_legacyxx",
        name="旧 Key",
        member_id=env["owner"].id,
        mode="unlimited",
        encrypted_key=None,
    )
    s.add(key)
    s.commit()
    key_id = key.id
    s.close()
    resp = env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        headers=_admin(env),
    )
    assert resp.status_code == 410


def test_revoke_writes_audit_event_and_blocks_patch(env):
    client = env["client"]
    key_id = _create_key(env).json()["id"]
    client.post(f"/api/v2/proxy-keys/{key_id}/revoke", headers=_admin(env))
    resp = client.patch(
        f"/api/v2/proxy-keys/{key_id}", json={"name": "k2"}, headers=_admin(env)
    )
    assert resp.status_code == 409
    s = env["sf"]()
    from pulse.storage.models import ProxyEvent

    assert s.query(ProxyEvent).filter_by(event_type="revoked", proxy_key_id=key_id).count() == 1
    s.close()


def test_negative_limit_rejected(env):
    resp = env["client"].post(
        "/api/v2/proxy-keys",
        json={"member_id": env["owner"].id, "mode": "quota", "token_limit": -5},
        headers=_admin(env),
    )
    assert resp.status_code == 422


def test_create_requires_member_id(env):
    resp = env["client"].post(
        "/api/v2/proxy-keys",
        json={"mode": "unlimited"},
        headers=_admin(env),
    )
    assert resp.status_code == 422
