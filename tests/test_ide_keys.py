from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
from pulse.ingestion.crypto import encrypt_secret
from pulse.proxy import service as proxy_service
from pulse.proxy.ide_keys import find_ide_key_parent, get_or_issue_ide_key, rotate_ide_key
from pulse.proxy.keys import generate_ide_key, generate_proxy_key, hash_proxy_key
from pulse.proxy.seat_assignment import _member_id_for_plaintext
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    AiAccountCredential,
    AiPlan,
    AiVendor,
    KeyLoan,
    Member,
    ProxyKey,
    ProxyKeyUsage,
)
from pulse.web.app import create_app
from pulse.web.portal import bootstrap_portal_owner
from sqlalchemy import select
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 7, 22)

CURSOR_UNDER_ALIAS = "crsr_bound_under_alias_ide_equiv_yy"


def _healthy_snap(account_id: str, *, cycle_end: date) -> AccountQuotaSnapshot:
    return AccountQuotaSnapshot(
        account_id=account_id,
        captured_at=NOW,
        cycle_start=TODAY - timedelta(days=10),
        cycle_end=cycle_end,
        limit_cents=7000,
        used_cents=700,
        remaining_cents=6300,
        total_pct=10.0,
        auto_pct=10.0,
        api_pct=5.0,
    )


@pytest.fixture(scope="module")
def ide_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    client, proxy = make_module_web_client(config)
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    borrower = repo.add_member("borrower-ide", "Borrower")
    borrower.portal_role = "ai_member"
    borrower.portal_status = "active"
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
        account_identifier="acct-ide-1",
        team_id=team.id,
        proxy_enabled=True,
    )
    s.add(account)
    s.flush()
    cred = AiAccountCredential(
        account_id=account.id,
        vendor_id=vendor.id,
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-ide-1", TEST_KEY),
        key_hint="cur...e-1",
        key_role="primary",
        bound_by_member_id=owner.id,
        proxy_enabled=True,
    )
    s.add(cred)
    s.add(_healthy_snap(account.id, cycle_end=TODAY + timedelta(days=15)))
    s.commit()
    s.close()
    return {
        "client": client,
        "config": config,
        "sf": sf,
        "owner": owner,
        "borrower": borrower,
        "cred_id": cred.id,
        "account_id": account.id,
        "vendor_id": vendor.id,
        "team_id": team.id,
        "plan_id": plan.id,
    }


def _h(token: str = "internal-token") -> dict:
    return {"Authorization": f"Bearer {token}"}


def _issue_ide_for_key(session, key: ProxyKey) -> str:
    return get_or_issue_ide_key(session, key, TEST_KEY)


def _seed_proxy_key(session, **kwargs) -> tuple[ProxyKey, str, str]:
    plaintext, _, _ = generate_proxy_key()
    key = ProxyKey(
        key_hash=hash_proxy_key(plaintext),
        key_hint=plaintext[:11],
        encrypted_key=encrypt_secret(plaintext, TEST_KEY),
        name=kwargs.pop("name", "k"),
        member_id=kwargs.pop("member_id", "m1"),
        mode=kwargs.pop("mode", "quota"),
        **kwargs,
    )
    session.add(key)
    session.flush()
    ide = get_or_issue_ide_key(session, key, TEST_KEY)
    return key, plaintext, ide


def _seed_loan_alias(
    session, env, *, loan_status: str = "active", lender_mode: str = "manual", routing_mode: str = "pinned"
):
    from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS

    alias_plaintext = "pka_" + secrets.token_urlsafe(24)
    credential_id = None
    if routing_mode != "pool":
        cred = AiAccountCredential(
            account_id=env["account_id"],
            vendor_id=env["vendor_id"],
            credential_type="cursor_api_key",
            encrypted_value=encrypt_secret(CURSOR_UNDER_ALIAS, TEST_KEY),
            key_hint="crs...yy",
            key_role="loan",
            status="active",
            key_hash=hash_proxy_key(CURSOR_UNDER_ALIAS),
            bound_by_member_id=env["owner"].id,
        )
        session.add(cred)
        session.flush()
        credential_id = cred.id
    loan = KeyLoan(
        source_account_id=env["account_id"] if routing_mode != "pool" else None,
        credential_id=credential_id,
        routing_mode=routing_mode,
        status=loan_status,
        lender_mode=lender_mode,
        delivery_mode=DELIVERY_PROXY_ALIAS,
        borrower_member_id=env["borrower"].id,
        alias_key_hash=hash_proxy_key(alias_plaintext),
        alias_key_hint=alias_plaintext[:12],
        alias_encrypted_key=encrypt_secret(alias_plaintext, TEST_KEY),
    )
    session.add(loan)
    session.flush()
    ide = get_or_issue_ide_key(session, loan, TEST_KEY)
    return loan, alias_plaintext, ide


def _assert_equiv(parent_auth: dict, ide_auth: dict) -> None:
    assert ide_auth.pop("scope") == "ide"
    assert ide_auth == parent_auth


def test_generate_ide_key_format():
    plaintext, key_hash, hint = generate_ide_key()
    assert plaintext.startswith("pkide_")
    assert key_hash == hash_proxy_key(plaintext)
    assert hint.startswith("pkide_")
    assert len(hint) == 14


@pytest.mark.parametrize(
    "key_kwargs,usage_kwargs,expected_status,expected_reason",
    [
        ({}, None, "ok", None),
        ({"status": "suspended", "suspended_reason": "manual"}, None, "suspended", "manual"),
        ({"status": "revoked"}, None, "invalid", "revoked"),
        ({"expires_at": NOW - timedelta(seconds=1)}, None, "invalid", "expired"),
        (
            {"window_5h_cost_limit_cents": 1000},
            {"cost_cents": 1000, "hours_ago": 1},
            "ok",
            None,
        ),
        (
            {"window_7d_cost_limit_cents": 2000},
            {"cost_cents": 2000, "days_ago": 2},
            "ok",
            None,
        ),
    ],
)
def test_pkide_authorize_equiv_proxy_key(ide_env, key_kwargs, usage_kwargs, expected_status, expected_reason):
    s = ide_env["sf"]()
    key, parent_plain, ide_plain = _seed_proxy_key(s, member_id=ide_env["owner"].id, **key_kwargs)
    if usage_kwargs:
        ts = NOW - timedelta(hours=usage_kwargs.get("hours_ago", 0), days=usage_kwargs.get("days_ago", 0))
        s.add(ProxyKeyUsage(proxy_key_id=key.id, total_tokens=1, cost_cents=usage_kwargs["cost_cents"], ts=ts))
    s.commit()
    s.close()

    s = ide_env["sf"]()
    parent_auth = proxy_service.authorize_status(s, parent_plain, now=NOW, encryption_key=TEST_KEY)
    ide_auth = proxy_service.authorize_status(s, ide_plain, now=NOW, encryption_key=TEST_KEY)
    s.close()
    assert parent_auth["status"] == expected_status
    assert parent_auth.get("reason") == expected_reason
    _assert_equiv(parent_auth, ide_auth)

    if expected_status == "ok":
        resp = ide_env["client"].post(
            "/api/internal/v1/proxy/authorize",
            json={"pulse_key": ide_plain},
            headers=_h(),
        )
        http_auth = resp.json()
        assert http_auth["scope"] == "ide"
        assert http_auth["status"] == expected_status


@pytest.mark.parametrize(
    "loan_kwargs,expected_status",
    [
        ({"lender_mode": "manual"}, "ok"),
        ({"lender_mode": "auto"}, "ok"),
        ({"routing_mode": "pool", "lender_mode": "auto"}, "ok"),
        ({"loan_status": "revoked"}, "invalid"),
    ],
)
def test_pkide_authorize_equiv_loan_alias(ide_env, loan_kwargs, expected_status):
    s = ide_env["sf"]()
    loan, parent_plain, ide_plain = _seed_loan_alias(s, ide_env, **loan_kwargs)
    s.commit()
    loan_id = loan.id
    s.close()

    s = ide_env["sf"]()
    parent_auth = proxy_service.authorize_status(s, parent_plain, encryption_key=TEST_KEY)
    ide_auth = proxy_service.authorize_status(s, ide_plain, encryption_key=TEST_KEY)
    s.close()
    assert parent_auth["status"] == expected_status
    _assert_equiv(parent_auth, ide_auth)
    if expected_status == "ok":
        assert ide_auth["loan_id"] == loan_id


def test_pkide_unknown_and_coding_plan_parent(ide_env):
    s = ide_env["sf"]()
    unknown = proxy_service.authorize_status(s, "pkide_not_a_real_key", now=NOW)
    assert unknown["reason"] == "unknown_key"
    assert "scope" not in unknown

    from pulse.proxy.key_crud import create_coding_plan_key

    key, _ = create_coding_plan_key(
        s, name="cp", member_id=ide_env["owner"].id, coding_plan_vendor="glm", encryption_key=TEST_KEY
    )
    with pytest.raises(ValueError, match="quota"):
        get_or_issue_ide_key(s, key, TEST_KEY)

    ide_plain, ide_hash, ide_hint = generate_ide_key()
    key.ide_key_hash = ide_hash
    key.ide_key_hint = ide_hint
    key.ide_encrypted_key = encrypt_secret(ide_plain, TEST_KEY)
    s.commit()
    s.close()

    s = ide_env["sf"]()
    ide_auth = proxy_service.authorize_status(s, ide_plain, now=NOW, encryption_key=TEST_KEY)
    s.close()
    assert ide_auth["status"] == "invalid"
    assert ide_auth["reason"] == "unknown_key"
    assert "scope" not in ide_auth


def test_pkide_seat_assignment_member_resolution(ide_env):
    s = ide_env["sf"]()
    key, _, ide_plain = _seed_proxy_key(s, member_id=ide_env["owner"].id)
    loan, _, loan_ide = _seed_loan_alias(s, ide_env)
    s.commit()
    s.close()

    s = ide_env["sf"]()
    assert _member_id_for_plaintext(s, ide_plain) == ide_env["owner"].id
    assert _member_id_for_plaintext(s, loan_ide) == ide_env["borrower"].id
    assert find_ide_key_parent(s, ide_plain) is not None
    s.close()


def _seed_proxy_addresses(session, team_id: str) -> None:
    from pulse.storage.models import TeamSetting

    existing = session.scalar(
        select(TeamSetting).where(TeamSetting.team_id == team_id, TeamSetting.section == "proxy_addresses")
    )
    data = {"addresses": [{"url": "http://wan.example:8317", "display_name": "公网"}]}
    if existing is None:
        session.add(TeamSetting(team_id=team_id, section="proxy_addresses", data=data))
    else:
        existing.data = data


def test_proxy_key_client_setup_ide_issues_pkide(ide_env):
    from pulse.web.auth_tokens import create_access_token

    s = ide_env["sf"]()
    _seed_proxy_addresses(s, ide_env["team_id"])
    key, parent_plain, _ = _seed_proxy_key(s, member_id=ide_env["owner"].id)
    s.commit()
    key_id = key.id
    s.close()

    token = create_access_token(ide_env["config"], ide_env["owner"])
    resp1 = ide_env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        params={"kind": "ide", "proxy_url": "http://wan.example:8317"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["kind"] == "ide"
    assert body1["plaintext_key"].startswith("pkide_")
    assert body1["plaintext_key"] != parent_plain
    assert "pkide_" in body1["command"]

    resp2 = ide_env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        params={"kind": "ide"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.json()["plaintext_key"] == body1["plaintext_key"]

    rotate = ide_env["client"].post(
        f"/api/v2/proxy-keys/{key_id}/ide-key/rotate",
        params={"proxy_url": "http://wan.example:8317"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rotate.status_code == 200
    new_key = rotate.json()["plaintext_key"]
    assert new_key.startswith("pkide_")
    assert new_key != body1["plaintext_key"]

    s = ide_env["sf"]()
    old_auth = proxy_service.authorize_status(s, body1["plaintext_key"], now=NOW, encryption_key=TEST_KEY)
    new_auth = proxy_service.authorize_status(s, new_key, now=NOW, encryption_key=TEST_KEY)
    s.close()
    assert old_auth["reason"] == "unknown_key"
    assert new_auth["status"] == "ok"


def test_loan_client_setup_ide_cursor_direct_unchanged(ide_env):
    from pulse.web.auth_tokens import create_access_token

    s = ide_env["sf"]()
    _seed_proxy_addresses(s, ide_env["team_id"])
    cr_plain = "crsr_direct_loan_key_for_ide_test"
    cred = AiAccountCredential(
        account_id=ide_env["account_id"],
        vendor_id=ide_env["vendor_id"],
        credential_type="cursor_api_key",
        encrypted_value=encrypt_secret(cr_plain, TEST_KEY),
        key_hint="crs...st",
        key_role="loan",
        status="active",
        key_hash=hash_proxy_key(cr_plain),
        bound_by_member_id=ide_env["borrower"].id,
    )
    s.add(cred)
    s.flush()
    loan = KeyLoan(
        source_account_id=ide_env["account_id"],
        credential_id=cred.id,
        status="active",
        borrower_member_id=ide_env["borrower"].id,
        delivery_mode="cursor_direct",
    )
    s.add(loan)
    s.commit()
    loan_id = loan.id
    s.close()

    token = create_access_token(ide_env["config"], ide_env["borrower"])
    resp = ide_env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        params={"kind": "ide"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plaintext_key"] == cr_plain
    assert body["plaintext_key"].startswith("cr")
    assert "pkide_" not in body["command"]


def test_loan_client_setup_proxy_alias_ide_issues_pkide_without_parent_secrets(ide_env):
    from pulse.web.auth_tokens import create_access_token

    s = ide_env["sf"]()
    _seed_proxy_addresses(s, ide_env["team_id"])
    loan, alias_plaintext, _ = _seed_loan_alias(s, ide_env)
    s.commit()
    loan_id = loan.id
    s.close()

    token = create_access_token(ide_env["config"], ide_env["borrower"])
    resp1 = ide_env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        params={"kind": "ide"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["kind"] == "ide"
    assert body1["plaintext_key"].startswith("pkide_")
    serialized = json.dumps(body1, ensure_ascii=False)
    assert alias_plaintext not in serialized
    assert CURSOR_UNDER_ALIAS not in serialized

    resp2 = ide_env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        params={"kind": "ide"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.json()["plaintext_key"] == body1["plaintext_key"]


def test_loan_ide_key_rotate_permissions_and_states(ide_env):
    from pulse.web.auth_tokens import create_access_token

    s = ide_env["sf"]()
    _seed_proxy_addresses(s, ide_env["team_id"])
    loan, _, _ = _seed_loan_alias(s, ide_env)
    s.commit()
    loan_id = loan.id
    s.close()

    borrower_token = create_access_token(ide_env["config"], ide_env["borrower"])
    setup = ide_env["client"].get(
        f"/api/v2/loans/{loan_id}/client-setup",
        params={"kind": "ide"},
        headers={"Authorization": f"Bearer {borrower_token}"},
    )
    old_pkide = setup.json()["plaintext_key"]

    rotate = ide_env["client"].post(
        f"/api/v2/loans/{loan_id}/ide-key/rotate",
        headers={"Authorization": f"Bearer {borrower_token}"},
    )
    assert rotate.status_code == 200
    new_pkide = rotate.json()["plaintext_key"]
    assert new_pkide.startswith("pkide_")
    assert new_pkide != old_pkide

    s = ide_env["sf"]()
    assert proxy_service.authorize_status(s, old_pkide, encryption_key=TEST_KEY)["reason"] == "unknown_key"
    assert proxy_service.authorize_status(s, new_pkide, encryption_key=TEST_KEY)["status"] == "ok"
    s.close()

    s = ide_env["sf"]()
    _, repo = make_team_repo(s)
    other = repo.add_member("other-ide-rotate", "Other")
    other.portal_role = "ai_member"
    other.portal_status = "active"
    s.commit()
    other_token = create_access_token(ide_env["config"], other)
    s.close()

    forbidden = ide_env["client"].post(
        f"/api/v2/loans/{loan_id}/ide-key/rotate",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert forbidden.status_code == 403

    s = ide_env["sf"]()
    _seed_proxy_addresses(s, ide_env["team_id"])
    cr_plain = "crsr_direct_rotate_guard_test_key"
    cred = AiAccountCredential(
        account_id=ide_env["account_id"],
        vendor_id=ide_env["vendor_id"],
        credential_type="cursor_api_key",
        encrypted_value=encrypt_secret(cr_plain, TEST_KEY),
        key_hint="crs...rt",
        key_role="loan",
        status="active",
        key_hash=hash_proxy_key(cr_plain),
        bound_by_member_id=ide_env["borrower"].id,
    )
    s.add(cred)
    s.flush()
    direct_loan = KeyLoan(
        source_account_id=ide_env["account_id"],
        credential_id=cred.id,
        status="active",
        borrower_member_id=ide_env["borrower"].id,
        delivery_mode="cursor_direct",
    )
    s.add(direct_loan)
    s.commit()
    direct_loan_id = direct_loan.id
    s.close()

    direct_rotate = ide_env["client"].post(
        f"/api/v2/loans/{direct_loan_id}/ide-key/rotate",
        headers={"Authorization": f"Bearer {borrower_token}"},
    )
    assert direct_rotate.status_code == 400

    s = ide_env["sf"]()
    loan = s.get(KeyLoan, loan_id)
    loan.status = "revoked"
    s.commit()
    s.close()

    inactive_rotate = ide_env["client"].post(
        f"/api/v2/loans/{loan_id}/ide-key/rotate",
        headers={"Authorization": f"Bearer {borrower_token}"},
    )
    assert inactive_rotate.status_code == 410


def test_proxy_key_client_setup_ide_works_when_parent_unrecoverable(ide_env):
    from pulse.web.auth_tokens import create_access_token

    parent_plain, _, _ = generate_proxy_key()
    s = ide_env["sf"]()
    _seed_proxy_addresses(s, ide_env["team_id"])
    key = ProxyKey(
        key_hash=hash_proxy_key(parent_plain),
        key_hint=parent_plain[:11],
        encrypted_key=None,
        name="legacy-unrecoverable",
        member_id=ide_env["owner"].id,
        mode="quota",
    )
    s.add(key)
    s.commit()
    key_id = key.id
    s.close()

    token = create_access_token(ide_env["config"], ide_env["owner"])
    ide_resp = ide_env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        params={"kind": "ide"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ide_resp.status_code == 200
    ide_body = ide_resp.json()
    assert ide_body["plaintext_key"].startswith("pkide_")
    serialized = json.dumps(ide_body, ensure_ascii=False)
    assert parent_plain not in serialized

    cli_resp = ide_env["client"].get(
        f"/api/v2/proxy-keys/{key_id}/client-setup",
        params={"kind": "cli"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cli_resp.status_code == 410
