from __future__ import annotations

import base64
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

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
    KeyLoan,
    ProxyKeyUsage,
)
from pulse.web.app import create_app
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

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


@pytest.fixture(scope="module")
def _internal_proxy_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def env(_internal_proxy_app):
    client, config, proxy = _internal_proxy_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")

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
        "client": client,
        "config": config,
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


def _seed_loan_alias(env, *, loan_status: str = "active", lender_mode: str = "manual"):
    """建一笔 pka_ 别名借用（loan Key + 别名哈希），返回 (cred_id, loan_id)。"""
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
        lender_mode=lender_mode,
        delivery_mode=DELIVERY_PROXY_ALIAS,
        alias_key_hash=hash_proxy_key(ALIAS_PLAINTEXT),
        alias_key_hint=ALIAS_PLAINTEXT[:12],
        alias_encrypted_key=encrypt_secret(ALIAS_PLAINTEXT, TEST_KEY),
    )
    s.add(loan)
    s.commit()
    s.close()
    return cred.id, loan.id


def test_authorize_loan_pool_skips_cursor_key(env):
    from pulse.tool_center.key_loans import DELIVERY_PROXY_ALIAS

    pool_key = "pka_pool_route_test_key"
    s = env["sf"]()
    loan = KeyLoan(
        source_account_id=None,
        credential_id=None,
        routing_mode="pool",
        lender_mode="auto",
        status="active",
        delivery_mode=DELIVERY_PROXY_ALIAS,
        alias_key_hash=hash_proxy_key(pool_key),
        alias_key_hint=pool_key[:12],
        alias_encrypted_key=encrypt_secret(pool_key, TEST_KEY),
        borrower_member_id=None,
    )
    s.add(loan)
    s.commit()
    loan_id = loan.id
    s.close()

    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": pool_key},
        headers=_h(),
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["mode"] == "loan_pool"
    assert body["loan_id"] == loan_id
    assert body["proxy_key_id"] is None
    assert body.get("cursor_api_key") in (None, "")
    assert body["credential_id"] is None


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


def test_authorize_loan_alias_manual_keeps_fixed_key(env):
    """指定借用（manual）：不下发候选白名单，仍固定在发放时那把 Cursor Key。"""
    cred_id, loan_id = _seed_loan_alias(env, lender_mode="manual")
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": ALIAS_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "ok"
    assert body["credential_id"] == cred_id
    assert body["cursor_api_key"] == CURSOR_UNDER_ALIAS
    assert body.get("credential_ids") in (None, [])
    assert body["loan_id"] == loan_id


def test_authorize_loan_alias_auto_lists_primary_credentials(env):
    """自动分配借用（auto）：下发候选账号的 primary 凭证，供 Go 游走选号。"""
    _seed_loan_alias(env, lender_mode="auto")
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": ALIAS_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "ok"
    # 白名单里是账号 primary 凭证，不是发放时那把 loan Key
    assert body["credential_ids"] == [env["cred_id"]]
    assert env["cred_id"] != body["credential_id"]


def test_loan_candidate_credentials_excludes_borrower_own_accounts(env):
    """借用人自己名下账号不得进入候选白名单。

    构造：借用人拥有 acct-1，本笔借用落在 acct-2 → 白名单里不应出现 acct-1
    （它不是当前绑定账号，因此不会被兜底逻辑加回来）。
    """
    from pulse.proxy.pool_board import loan_candidate_credentials, reset_loan_candidate_cache
    from pulse.storage.models import AiAccount, Member

    reset_loan_candidate_cache()
    _seed_loan_alias(env, lender_mode="auto")
    s = env["sf"]()
    borrower = Member(
        team_id=env["team_id"],
        display_name="Borrower",
        channel_user_id="borrower-cand",
        status="active",
    )
    s.add(borrower)
    s.flush()
    own_account = s.get(AiAccount, env["account_id"])
    own_account.primary_member_id = borrower.id

    other = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-cand-2",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    s.add(other)
    s.flush()
    other_cred = AiAccountCredential(
        account_id=other.id,
        vendor_id=env["vendor_id"],
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-cand-2", TEST_KEY),
        key_hint="can...d2",
        key_role="primary",
        status="active",
        bound_by_member_id=borrower.id,
    )
    s.add(other_cred)
    s.add(_healthy_snap(other.id, cycle_end=TODAY + timedelta(days=25), total_pct=20.0))
    s.flush()

    loan = s.query(KeyLoan).order_by(KeyLoan.created_at.desc()).first()
    loan.borrower_member_id = borrower.id
    loan.source_account_id = other.id
    s.commit()

    ids = loan_candidate_credentials(s, loan=loan)
    s.close()

    assert ids == [other_cred.id]
    assert env["cred_id"] not in ids


def test_loan_candidate_credentials_cached(env):
    """同一 loan 第二次调用命中缓存，reset 后缓存清空。"""
    from pulse.proxy.pool_board import (

        _loan_candidates,
        loan_candidate_credentials,
        reset_loan_candidate_cache,
    )

    reset_loan_candidate_cache()
    _seed_loan_alias(env, lender_mode="auto")
    s = env["sf"]()
    loan = s.query(KeyLoan).order_by(KeyLoan.created_at.desc()).first()
    first = loan_candidate_credentials(s, loan=loan)
    assert _loan_candidates  # 已写入缓存
    second = loan_candidate_credentials(s, loan=loan)
    assert first == second
    s.close()
    reset_loan_candidate_cache()
    assert not _loan_candidates


def test_loan_candidate_credentials_excludes_account_not_syncing(env):
    """同步不正常的账号不得被新选中游走（与 build_lender_candidates 同一口径）。

    构造：本笔借用落在 acct-1（同步正常），另有 acct-2 同步失败 → 白名单只应
    含 acct-1。acct-2 不是当前绑定账号，因此不会被兜底逻辑加回来。
    """
    from pulse.proxy.pool_board import loan_candidate_credentials, reset_loan_candidate_cache
    from pulse.storage.models import AiAccount

    reset_loan_candidate_cache()
    _seed_loan_alias(env, lender_mode="auto")
    s = env["sf"]()
    primary = s.get(AiAccountCredential, env["cred_id"])
    primary.last_sync_status = "success"
    broken = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-unsynced",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    s.add(broken)
    s.flush()
    s.add(
        AiAccountCredential(
            account_id=broken.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-broken", TEST_KEY),
            key_hint="bro...en",
            key_role="primary",
            status="active",
            last_sync_status="failed",
            bound_by_member_id="m1",
        )
    )
    s.add(_healthy_snap(broken.id, cycle_end=TODAY + timedelta(days=25), total_pct=5.0))
    s.commit()

    loan = s.query(KeyLoan).order_by(KeyLoan.created_at.desc()).first()
    ids = loan_candidate_credentials(s, loan=loan)
    s.close()
    reset_loan_candidate_cache()

    assert ids == [env["cred_id"]]


def test_authorize_loan_alias_auto_falls_back_to_whitelist_key(env):
    """auto 借用下发放时那把 loan Key 失效：仍要下发可用的 cursor_api_key。

    Go 的 handleExchange 对 loan_alias 强制要求 cursor_api_key，为空直接 500；
    因此兜底必须从白名单里解出一把 primary 凭证，而不是只回白名单 ID。
    """
    _seed_loan_alias(env, lender_mode="auto")
    s = env["sf"]()
    loan = s.query(KeyLoan).order_by(KeyLoan.created_at.desc()).first()
    loan_cred = s.get(AiAccountCredential, loan.credential_id)
    loan_cred.status = "revoked"
    loan_cred.encrypted_value = ""
    s.commit()
    s.close()

    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": ALIAS_PLAINTEXT},
        headers=_h(),
    )
    body = resp.json()
    assert body["status"] == "ok"
    assert body["credential_ids"] == [env["cred_id"]]
    assert body["credential_id"] == env["cred_id"]
    # 解出来的是账号 primary 凭证的明文，Go 用它换 JWT
    assert body["cursor_api_key"] == "cursor-key-1"


def test_loan_candidate_credentials_never_asks_jev(env):
    """白名单在请求路径上，不得触发 Jev（CONTEXT.md「Jev Decision」的不变量）。"""
    from unittest.mock import MagicMock, patch

    from pulse.proxy.pool_board import loan_candidate_credentials, reset_loan_candidate_cache

    reset_loan_candidate_cache()
    _seed_loan_alias(env, lender_mode="auto")
    fake_builder = MagicMock(return_value=None)
    with patch("pulse.web.internal_proxy_api.build_jev_client", fake_builder):
        resp = env["client"].post(
            "/api/internal/v1/proxy/authorize",
            json={"pulse_key": ALIAS_PLAINTEXT},
            headers=_h(),
        )
        assert resp.json()["status"] == "ok"
        fake_builder.assert_not_called()

        s = env["sf"]()
        loan = s.query(KeyLoan).order_by(KeyLoan.created_at.desc()).first()
        loan_candidate_credentials(s, loan=loan)
        s.close()
        fake_builder.assert_not_called()

        # 池刷新路径仍应构造 Jev 客户端，证明上面的断言不是因为打桩失效
        env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
        fake_builder.assert_called()
    reset_loan_candidate_cache()


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
    assert len(creds) == 1
    assert creds[0]["credential_id"] == env["cred_id"]
    assert creds[0]["api_key"] == "cursor-key-1"
    assert creds[0]["auto_pct"] == 10.0
    assert creds[0]["api_pct"] == 5.0


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


def test_usage_records_without_suspend(env):
    client, sf = env["client"], env["sf"]
    s = sf()
    key, _ = proxy_service.create_key(
        s, name="k", member_id="m1", window_5h_cost_limit_cents=100
    )
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
    # Window overage soft-rejects on authorize; usage recording no longer suspends.
    assert body["suspended"] == []
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
    assert len(creds) == 1
    assert creds[0]["credential_id"] == env["cred_id"]
    assert creds[0]["api_key"] == "cursor-key-1"
    assert creds[0]["auto_pct"] == 10.0
    assert creds[0]["api_pct"] == 5.0


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


def test_pool_orders_by_score_adjust(env):
    """人工微调后，MITM 下发顺序与打分表一致。"""
    s = env["sf"]()
    far = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-far",
        team_id=env["team_id"],
        proxy_enabled=True,
        proxy_score_adjust=1.0,
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
    s.add(
        AiAccountCredential(
            account_id=far.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-far", TEST_KEY),
            key_hint="far...xx",
            key_role="primary",
            status="active",
            bound_by_member_id="m1",
        )
    )
    s.flush()
    s.add(
        AiAccountCredential(
            account_id=soon.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-soon", TEST_KEY),
            key_hint="soon...xx",
            key_role="primary",
            status="active",
            bound_by_member_id="m1",
        )
    )
    s.add(_healthy_snap(soon.id, cycle_end=TODAY + timedelta(days=2), total_pct=20.0))
    s.add(_healthy_snap(far.id, cycle_end=TODAY + timedelta(days=25), total_pct=20.0))
    default = s.get(AiAccount, env["account_id"])
    default.proxy_enabled = False
    s.commit()
    s.close()

    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    assert [c["api_key"] for c in creds] == ["cursor-key-far", "cursor-key-soon"]


def test_pool_ranking_board_carries_owner_and_switch_recency(env):
    """代理池候选必须与借用路径同源：带上主负责人与驻留基准。

    漏填这两项会让驻留降权恒为 1.0，且 Jev 看到的 owner 退化成 unassigned。
    """
    from datetime import datetime, timezone

    from pulse.proxy.pool_board import list_pool_ranking_board
    from pulse.storage.models import KeyLoan, Member

    s = env["sf"]()
    owner_member = Member(
        team_id=env["team_id"],
        display_name="Owner One",
        channel_user_id="owner-one",
        status="active",
    )
    s.add(owner_member)
    s.flush()
    account = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-owned",
        team_id=env["team_id"],
        proxy_enabled=True,
        primary_member_id=owner_member.id,
    )
    s.add(account)
    s.flush()
    s.add(
        AiAccountCredential(
            account_id=account.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-owned", TEST_KEY),
            key_hint="own...xx",
            key_role="primary",
            status="active",
            bound_by_member_id=owner_member.id,
        )
    )
    s.add(_healthy_snap(account.id, cycle_end=TODAY + timedelta(days=25), total_pct=20.0))
    # 一笔刚绑定的借用：该账号应带上驻留基准
    s.add(
        KeyLoan(
            source_account_id=account.id,
            credential_id="cred-owned",
            borrower_member_id=owner_member.id,
            baseline_used_cents=0,
            status="active",
            lender_mode="manual",
            source_bound_at=datetime.now(timezone.utc),
        )
    )
    default = s.get(AiAccount, env["account_id"])
    default.proxy_enabled = False
    s.commit()

    board = list_pool_ranking_board(s)
    s.close()

    row = next(r for r in board["ranked"] if r["account_identifier"] == "acct-owned")
    assert row["primary_member_name"] == "Owner One"
    assert row["minutes_since_switch"] is not None
    assert row["minutes_since_switch"] < 5.0


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
            auto_pct=100.0,
            api_pct=100.0,
        )
    )
    s.commit()
    s.close()

    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    keys = {c["api_key"] for c in resp.json()["credentials"]}
    assert "cursor-key-1" in keys
    assert "cursor-key-ex" not in keys
    assert "cursor-key-nosnap" not in keys


def test_pool_keeps_total_exhausted_when_one_bucket_has_headroom(env):
    """Intake OR: total_pct=100 but api_pct still open → credential stays in pool."""
    s = env["sf"]()
    mixed = AiAccount(
        vendor_id=env["vendor_id"],
        plan_id=env["plan_id"],
        account_identifier="acct-mixed-bucket",
        team_id=env["team_id"],
        proxy_enabled=True,
    )
    s.add(mixed)
    s.flush()
    s.add(
        AiAccountCredential(
            account_id=mixed.id,
            vendor_id=env["vendor_id"],
            credential_type="api_key",
            encrypted_value=encrypt_secret("cursor-key-mixed", TEST_KEY),
            key_hint="mx...xx",
            key_role="primary",
            status="active",
            bound_by_member_id="m1",
        )
    )
    s.add(
        AccountQuotaSnapshot(
            account_id=mixed.id,
            captured_at=NOW,
            cycle_start=TODAY - timedelta(days=20),
            cycle_end=TODAY + timedelta(days=10),
            limit_cents=7000,
            used_cents=7000,
            remaining_cents=0,
            total_pct=100.0,
            auto_pct=100.0,
            api_pct=40.0,
        )
    )
    s.commit()
    s.close()

    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 200
    keys = {c["api_key"] for c in resp.json()["credentials"]}
    assert "cursor-key-mixed" in keys
    mixed_row = next(c for c in resp.json()["credentials"] if c["api_key"] == "cursor-key-mixed")
    assert mixed_row["auto_pct"] == 100.0
    assert mixed_row["api_pct"] == 40.0


def _seat_keys(env, n: int, *, same_member: bool = False) -> list[str]:
    from pulse.storage.models import Member

    s = env["sf"]()
    plains = []
    shared = None
    for i in range(n):
        if same_member and shared is not None:
            member_id = shared
        else:
            member = Member(
                team_id=env["team_id"],
                display_name=f"Seat {i}",
                channel_user_id=f"seat-{i}-{n}",
                status="active",
            )
            s.add(member)
            s.flush()
            member_id = member.id
            shared = member_id
        _key, plain = proxy_service.create_key(s, name=f"seat-{i}", member_id=member_id)
        plains.append(plain)
    s.commit()
    s.close()
    return plains


def _authorize(env, pulse_key: str, **extra) -> dict:
    resp = env["client"].post(
        "/api/internal/v1/proxy/authorize",
        json={"pulse_key": pulse_key, **extra},
        headers=_h(),
    )
    assert resp.status_code == 200
    return resp.json()


def test_authorize_reports_current_credential_and_caps_seats(env):
    from pulse.proxy.occupancy import reset_occupancy
    from pulse.settings.team_store import patch_team_setting

    reset_occupancy()
    s = env["sf"]()
    patch_team_setting(
        s,
        team_id=env["team_id"],
        section="tool_center",
        patch={"loan_selection": {"max_concurrent_users": 1}},
        member_id=None,
    )
    s.commit()
    s.close()

    first, second = _seat_keys(env, 2)
    one = _authorize(env, first)
    assert one["status"] == "ok"
    assert one["seat_advised"] is True
    assert one["max_concurrent_users"] == 1
    assert one["assigned_credential_id"] == env["cred_id"]

    kept = _authorize(env, first, current_credential_id=env["cred_id"])
    assert kept["assigned_credential_id"] == env["cred_id"]

    other = _authorize(env, second)
    assert other["assigned_credential_id"] in (None, "")
    assert env["cred_id"] in other["blocked_credential_ids"]

    released = _authorize(
        env, first, current_credential_id=env["cred_id"], release_current=True
    )
    assert released["assigned_credential_id"] in (None, "")
    took = _authorize(env, second)
    assert took["assigned_credential_id"] == env["cred_id"]


def test_authorize_same_member_is_one_seat(env):
    from pulse.proxy.occupancy import reset_occupancy
    from pulse.settings.team_store import patch_team_setting

    reset_occupancy()
    s = env["sf"]()
    patch_team_setting(
        s,
        team_id=env["team_id"],
        section="tool_center",
        patch={"loan_selection": {"max_concurrent_users": 1}},
        member_id=None,
    )
    s.commit()
    s.close()
    first, second = _seat_keys(env, 2, same_member=True)
    assert _authorize(env, first)["assigned_credential_id"] == env["cred_id"]
    assert _authorize(env, second)["assigned_credential_id"] == env["cred_id"]


def test_authorize_pinned_loan_keeps_seat_when_account_is_full(env):
    from pulse.proxy.occupancy import reset_occupancy
    from pulse.settings.team_store import patch_team_setting

    reset_occupancy()
    s = env["sf"]()
    patch_team_setting(
        s,
        team_id=env["team_id"],
        section="tool_center",
        patch={"loan_selection": {"max_concurrent_users": 1}},
        member_id=None,
    )
    s.commit()
    s.close()
    (plain,) = _seat_keys(env, 1)
    assert _authorize(env, plain)["assigned_credential_id"] == env["cred_id"]

    loan_cred, _loan_id = _seed_loan_credential(env)
    body = _authorize(env, LOAN_PLAINTEXT, current_credential_id=loan_cred)
    assert body["status"] == "ok"
    assert body["mode"] == "loan_passthrough"
    assert body["seat_advised"] is True
    assert body["assigned_credential_id"] == loan_cred
