from __future__ import annotations

import base64
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
from pulse.ingestion.crypto import encrypt_secret
from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.proxy import service as proxy_service
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    AiAccountCredential,
    AiPlan,
    AiVendor,
    Base,
    KeyLoan,
    ProxyKeyUsage,
)
from pulse.web.app import create_app
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
TODAY = date(2026, 7, 22)


def _healthy_snap(
    account_id: str,
    *,
    cycle_end: date,
    total_pct: float = 10.0,
    cycle_start: date | None = None,
) -> AccountQuotaSnapshot:
    start = cycle_start or (TODAY - timedelta(days=10))
    return AccountQuotaSnapshot(
        account_id=account_id,
        captured_at=NOW,
        cycle_start=start,
        cycle_end=cycle_end,
        limit_cents=7000,
        used_cents=700,
        remaining_cents=6300,
        total_pct=total_pct,
        auto_pct=total_pct,
        api_pct=5.0,
    )


@pytest.fixture
def env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")

    vendor = AiVendor(slug="cursor", name="Cursor")
    s.add(vendor)
    s.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        plan_name="Pro",
        slug="pro",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
    )
    s.add(plan)
    s.flush()
    account = AiAccount(
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="acct-1",
        team_id=team.id,
        proxy_enabled=True,
    )
    s.add(account)
    s.flush()
    cred = AiAccountCredential(
        account_id=account.id,
        vendor_id=vendor.id,
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-1", TEST_KEY),
        key_hint="cur...y-1",
        bound_by_member_id=owner.id,
        proxy_enabled=True,
    )
    s.add(cred)
    # 入池需有合格配额快照（与借用 recommend_lenders 硬过滤一致）
    s.add(_healthy_snap(account.id, cycle_end=TODAY + timedelta(days=15)))
    s.commit()
    s.close()
    return {
        "client": TestClient(create_app(config, sf)),
        "sf": sf,
        "cred_id": cred.id,
        "account_id": account.id,
        "vendor_id": vendor.id,
        "team_id": team.id,
        "plan_id": plan.id,
    }


def _h(token: str = "internal-token") -> dict:
    return {"Authorization": f"Bearer {token}"}


LOAN_PLAINTEXT = "crsr_test_loan_key_abc"


def _seed_loan_credential(env, *, loan_status: str = "active", cred_status: str = "active"):
    s = env["sf"]()
    cred = AiAccountCredential(
        account_id=env["account_id"],
        vendor_id=env["vendor_id"],
        credential_type="cursor_api_key",
        encrypted_value=encrypt_secret(LOAN_PLAINTEXT, TEST_KEY),
        key_hint="crs...abc",
        key_role="loan",
        status=cred_status,
        key_hash=hash_proxy_key(LOAN_PLAINTEXT),
        bound_by_member_id="m1",
    )
    s.add(cred)
    s.flush()
    loan = KeyLoan(
        source_account_id=env["account_id"],
        credential_id=cred.id,
        status=loan_status,
    )
    s.add(loan)
    s.commit()
    s.close()
    return cred.id, loan.id


def test_authorize_requires_token(env):
    resp = env["client"].post("/api/internal/v1/proxy/authorize", json={"pulse_key": "pk_x"})
    assert resp.status_code == 401


def test_authorize_unknown_and_ok(env):
    client, sf = env["client"], env["sf"]
    resp = client.post("/api/internal/v1/proxy/authorize", json={"pulse_key": "pk_x"}, headers=_h())
    assert resp.json()["status"] == "invalid"

    s = sf()
    key, plaintext = proxy_service.create_key(s, name="k", member_id="m1", mode="unlimited")
    s.commit()
    s.close()
    resp = client.post("/api/internal/v1/proxy/authorize", json={"pulse_key": plaintext}, headers=_h())
    body = resp.json()
    assert body["status"] == "ok"
    assert body["proxy_key_id"] == key.id


def test_authorize_loan_passthrough_ok(env):
    cred_id, loan_id = _seed_loan_credential(env)
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": LOAN_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "loan_passthrough"
    assert body["proxy_key_id"] is None
    assert body["loan_id"] == loan_id
    assert body["credential_id"] == cred_id
    assert body["reason"] is None


ALIAS_PLAINTEXT = "pka_test_alias_key_for_authorize_xx"
CURSOR_UNDER_ALIAS = "crsr_bound_under_alias_key_yy"


def _seed_loan_alias(env, *, loan_status: str = "active"):
    from pulse.tool_center.key_loans import DELIVERY_PROXY_ALIAS

    s = env["sf"]()
    cred = AiAccountCredential(
        account_id=env["account_id"],
        vendor_id=env["vendor_id"],
        credential_type="cursor_api_key",
        encrypted_value=encrypt_secret(CURSOR_UNDER_ALIAS, TEST_KEY),
        key_hint="crs...yy",
        key_role="loan",
        status="active",
        key_hash=hash_proxy_key(CURSOR_UNDER_ALIAS),
        bound_by_member_id="m1",
    )
    s.add(cred)
    s.flush()
    loan = KeyLoan(
        source_account_id=env["account_id"],
        credential_id=cred.id,
        status=loan_status,
        delivery_mode=DELIVERY_PROXY_ALIAS,
        alias_key_hash=hash_proxy_key(ALIAS_PLAINTEXT),
        alias_key_hint=ALIAS_PLAINTEXT[:12],
        alias_encrypted_key=encrypt_secret(ALIAS_PLAINTEXT, TEST_KEY),
    )
    s.add(loan)
    s.commit()
    s.close()
    return cred.id, loan.id


def test_authorize_loan_alias_ok(env):
    cred_id, loan_id = _seed_loan_alias(env)
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": ALIAS_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "loan_alias"
    assert body["proxy_key_id"] is None
    assert body["loan_id"] == loan_id
    assert body["credential_id"] == cred_id
    assert body["cursor_api_key"] == CURSOR_UNDER_ALIAS
    assert body["reason"] is None


def test_authorize_loan_alias_does_not_hit_pk_pool(env):
    """pka_ must not be treated as pk_ shared-pool key."""
    _seed_loan_alias(env)
    # Also create a pool key that would never match
    s = env["sf"]()
    proxy_service.create_key(s, name="pool", member_id="m1", mode="unlimited")
    s.commit()
    s.close()
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": ALIAS_PLAINTEXT},
        headers=_h(),
    )
    assert resp.json()["mode"] == "loan_alias"


def test_authorize_loan_alias_revoked_invalid(env):
    _seed_loan_alias(env, loan_status="revoked")
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": ALIAS_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["reason"] == "unknown_key"


def test_authorize_proxy_alias_cursor_key_rejects_passthrough(env):
    """proxy_alias 的底层 cr* 不得走 loan_passthrough。"""
    _seed_loan_alias(env)
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": CURSOR_UNDER_ALIAS},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["reason"] == "alias_required"


def test_authorize_loan_revoked_invalid(env):
    _seed_loan_credential(env, loan_status="revoked")
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": LOAN_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["mode"] is None
    assert body["reason"] == "loan_inactive"


def test_authorize_unknown_cr_invalid(env):
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": "crsr_unknown"},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["mode"] is None
    assert body["reason"] == "unknown_key"


def test_pool_returns_only_enabled_credentials(env):
    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    assert creds == [{"credential_id": env["cred_id"], "api_key": "cursor-key-1"}]


def test_pool_excludes_loan_credentials(env):
    """借用 Key 不入代理池，仅 primary 入池。"""
    s = env["sf"]()
    s.add(
        AiAccountCredential(
            account_id=env["account_id"],
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-loan-key", TEST_KEY),
            key_hint="cur...loan",
            key_role="loan",
            status="active",
            proxy_enabled=True,
            bound_by_member_id="m1",
        )
    )
    s.commit()
    s.close()

    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    assert len(creds) == 1
    assert creds[0]["credential_id"] == env["cred_id"]
    assert creds[0]["api_key"] == "cursor-key-1"


def test_record_usage_by_loan_id(env):
    cred_id, loan_id = _seed_loan_credential(env)
    resp = env["client"].post(
        "/api/internal/v1/proxy/usage",
        json={
            "items": [
                {
                    "loan_id": loan_id,
                    "credential_id": cred_id,
                    "model": "claude-sonnet-4",
                    "tokens": {"input": 1, "output": 0},
                    "ts": NOW.isoformat(),
                }
            ]
        },
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recorded"] == 1
    assert body["suspended"] == []
    s = env["sf"]()
    usage = s.query(ProxyKeyUsage).one()
    assert usage.proxy_key_id is None
    assert usage.loan_id == loan_id
    assert usage.credential_id == cred_id
    assert usage.cost_cents >= 0
    s.close()


def test_record_usage_missing_both_ids_skipped(env):
    resp = env["client"].post(
        "/api/internal/v1/proxy/usage",
        json={"items": [{"tokens": {"input": 1}}]},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"recorded": 0, "suspended": []}
    s = env["sf"]()
    assert s.query(ProxyKeyUsage).count() == 0
    s.close()


def test_usage_records_and_suspends(env):
    client, sf = env["client"], env["sf"]
    s = sf()
    key, _ = proxy_service.create_key(s, name="k", member_id="m1", mode="quota", token_limit=100)
    s.commit()
    s.close()
    resp = client.post(
        "/api/internal/v1/proxy/usage",
        json={
            "items": [
                {
                    "proxy_key_id": key.id,
                    "credential_id": env["cred_id"],
                    "model": "claude-sonnet-4",
                    "tokens": {"input": 150, "output": 10},
                    "ts": NOW.isoformat(),
                }
            ]
        },
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recorded"] == 1
    assert body["suspended"] == [key.id]
    s = sf()
    assert s.query(ProxyKeyUsage).count() == 1
    s.close()


def test_events_endpoint(env):
    resp = env["client"].post(
        "/api/internal/v1/proxy/events",
        json={"events": [{"event_type": "exhausted", "credential_id": env["cred_id"], "detail": "usage_limit"}]},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"recorded": 1}


def test_usage_idempotent_on_retry(env):
    client, sf = env["client"], env["sf"]
    s = sf()
    key, _ = proxy_service.create_key(s, name="k", member_id="m1", mode="unlimited")
    s.commit()
    s.close()
    item = {
        "proxy_key_id": key.id,
        "credential_id": env["cred_id"],
        "model": "claude-sonnet-4",
        "tokens": {"input": 100},
        "request_id": "req-123",
    }
    r1 = client.post("/api/internal/v1/proxy/usage", json={"items": [item]}, headers=_h())
    r2 = client.post("/api/internal/v1/proxy/usage", json={"items": [item]}, headers=_h())
    assert r1.json() == {"recorded": 1, "suspended": []}
    assert r2.json() == {"recorded": 0, "suspended": []}
    s = sf()
    assert s.query(ProxyKeyUsage).count() == 1
    s.close()


def test_pool_excludes_disabled_account_and_undecryptable(env):
    client, sf = env["client"], env["sf"]
    s = sf()
    vendor_id = env["vendor_id"]
    plan = s.query(AiPlan).filter_by(slug="pro").one()
    disabled_acct = AiAccount(
        vendor_id=vendor_id,
        plan_id=plan.id,
        account_identifier="acct-disabled",
        proxy_enabled=False,
    )
    s.add(disabled_acct)
    s.flush()
    cred_disabled = AiAccountCredential(
        account_id=disabled_acct.id,
        vendor_id=vendor_id,
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-disabled", TEST_KEY),
        key_hint="bad...xx",
        bound_by_member_id="m1",
        proxy_enabled=True,  # 凭证级开启也不入池（账号未开）
    )
    cred_broken = AiAccountCredential(
        account_id=env["account_id"],
        vendor_id=vendor_id,
        credential_type="api_key",
        encrypted_value="garbage-not-decryptable",
        key_hint="brk...xx",
        bound_by_member_id="m1",
    )
    s.add_all([cred_disabled, cred_broken])
    s.commit()
    s.close()
    resp = client.get("/api/internal/v1/proxy/pool", headers=_h())
    creds = resp.json()["credentials"]
    assert creds == [{"credential_id": env["cred_id"], "api_key": "cursor-key-1"}]


def test_internal_token_header_and_503(env):
    # X-Pulse-Internal-Token 头路径
    resp = env["client"].get(
        "/api/internal/v1/proxy/pool", headers={"X-Pulse-Internal-Token": "internal-token"}
    )
    assert resp.status_code == 200
    # 错误 token
    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h("wrong"))
    assert resp.status_code == 401


def test_pool_503_when_encryption_key_missing(env):
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=""),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    client = TestClient(create_app(config, env["sf"]))
    resp = client.get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 503


def test_pool_orders_by_lender_recommend_urgency(env):
    """快到期且有余量的账号排在更前（同源 recommend_lenders）。

    故意先插入 far（更早 bound_at），确认不是按绑定时间而是按推荐分。
    """
    s = env["sf"]()
    far = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-far",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    soon = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-soon",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    s.add_all([far, soon])
    s.flush()
    cred_far = AiAccountCredential(
        account_id=far.id,
        vendor_id=env["vendor_id"],
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-far", TEST_KEY),
        key_hint="far...xx",
        key_role="primary",
        status="active",
        bound_by_member_id="m1",
    )
    s.add(cred_far)
    s.flush()
    cred_soon = AiAccountCredential(
        account_id=soon.id,
        vendor_id=env["vendor_id"],
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-soon", TEST_KEY),
        key_hint="soon...xx",
        key_role="primary",
        status="active",
        bound_by_member_id="m1",
    )
    s.add(cred_soon)
    # soon: 2 天后重置；far: 25 天后 — urgency 更高应排前
    s.add(_healthy_snap(soon.id, cycle_end=TODAY + timedelta(days=2), total_pct=20.0))
    s.add(_healthy_snap(far.id, cycle_end=TODAY + timedelta(days=25), total_pct=20.0))
    # 关掉默认账号，避免干扰排序断言
    default = s.get(AiAccount, env["account_id"])
    default.proxy_enabled = False
    s.commit()
    s.close()

    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    assert [c["api_key"] for c in creds] == ["cursor-key-soon", "cursor-key-far"]


def test_pool_hard_filters_exhausted_and_no_snapshot(env):
    s = env["sf"]()
    exhausted = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-exhausted",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    no_snap = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-nosnap",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    s.add_all([exhausted, no_snap])
    s.flush()
    s.add(
        AiAccountCredential(
            account_id=exhausted.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-ex", TEST_KEY),
            key_hint="ex...xx",
            key_role="primary",
            status="active",
            bound_by_member_id="m1",
        )
    )
    s.add(
        AiAccountCredential(
            account_id=no_snap.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-nosnap", TEST_KEY),
            key_hint="ns...xx",
            key_role="primary",
            status="active",
            bound_by_member_id="m1",
        )
    )
    s.add(
        AccountQuotaSnapshot(
            account_id=exhausted.id,
            captured_at=NOW,
            cycle_start=TODAY - timedelta(days=20),
            cycle_end=TODAY + timedelta(days=10),
            limit_cents=7000,
            used_cents=7000,
            remaining_cents=0,
            total_pct=100.0,
        )
    )
    s.commit()
    s.close()

    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    keys = {c["api_key"] for c in resp.json()["credentials"]}
    assert "cursor-key-1" in keys
    assert "cursor-key-ex" not in keys
    assert "cursor-key-nosnap" not in keys
