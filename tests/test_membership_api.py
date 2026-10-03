from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.proxy import service as proxy_service
from pulse.proxy.credit import charge_usage, grant
from pulse.proxy.membership import active_membership, cancel_membership, open_membership
from pulse.proxy.team_membership import membership_required_for_team, set_membership_required
from pulse.storage.models import (
    AdminAuditLog,
    CreditTransaction,
    KeyLoan,
    MembershipPlan,
    ProxyKey,
    ProxyKeyUsage,
    Team,
)
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
MIGRATED_MSG = "用量限制已迁移到会员，请在「会员」中设置"


@pytest.fixture(scope="module")
def _membership_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def env(_membership_app):
    client, config, proxy = _membership_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    member = repo.add_member("member1", "Member One")
    member.portal_role = "ai_member"
    member.portal_status = "active"
    member.status = "active"
    other_team = Team(slug="other", name="Other")
    s.add(other_team)
    s.flush()
    outsider = repo.add_member("outsider", "Outsider")
    outsider.team_id = other_team.id
    outsider.portal_role = "ai_member"
    outsider.portal_status = "active"
    outsider.status = "active"
    s.commit()
    s.close()
    return {
        "client": client,
        "config": config,
        "owner": owner,
        "member": member,
        "outsider": outsider,
        "team": team,
        "sf": sf,
    }


def _auth(config, member) -> dict:
    return {"Authorization": f"Bearer {create_access_token(config, member)}"}


def _admin(env):
    return _auth(env["config"], env["owner"])


def _member(env):
    return _auth(env["config"], env["member"])


def test_admin_can_create_plan_and_list(env):
    resp = env["client"].post(
        "/api/v2/membership-plans",
        headers=_admin(env),
        json={
            "name": "标准",
            "rules": [{"period": "week", "pool": "auto", "cost_usd": 10}],
            "credit_mode": "prepaid",
            "opening_credit_usd": 5.5,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["opening_credit_cents"] == 550
    assert body["rules"][0]["limit_cents"] == 1000

    listed = env["client"].get("/api/v2/membership-plans", headers=_admin(env))
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


def test_member_forbidden_on_admin_plans(env):
    resp = env["client"].get("/api/v2/membership-plans", headers=_member(env))
    assert resp.status_code == 403


def test_grant_validation_and_audit(env):
    resp = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/grants",
        headers=_admin(env),
        json={"amount_usd": 0},
    )
    assert resp.status_code == 422

    resp = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/grants",
        headers=_admin(env),
        json={"amount_usd": 1.234},
    )
    assert resp.status_code == 400

    resp = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/grants",
        headers=_admin(env),
        json={"amount_usd": 10, "note": "测试充值"},
    )
    assert resp.status_code == 201
    txn = resp.json()
    assert txn["kind"] == "grant"
    assert txn["amount_cents"] == 1000

    s = env["sf"]()
    audit = s.query(AdminAuditLog).filter_by(action="credit.grant").one()
    assert audit.member_id == env["owner"].id
    s.close()


def test_adjust_requires_note(env):
    resp = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/adjustments",
        headers=_admin(env),
        json={"amount_usd": -1, "note": "  "},
    )
    assert resp.status_code == 400


def test_put_open_change_clear_and_cancel(env):
    plan_resp = env["client"].post(
        "/api/v2/membership-plans",
        headers=_admin(env),
        json={"name": "A", "rules": [], "credit_mode": "unlimited"},
    )
    plan_id = plan_resp.json()["id"]

    put = env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={
            "plan_id": plan_id,
            "rules_override": [{"period": "5h", "pool": "total", "cost_usd": 2}],
            "credit_mode_override": "prepaid",
        },
    )
    assert put.status_code == 200
    row = put.json()
    assert row["membership"]["plan_id"] == plan_id
    assert row["membership"]["credit_mode"] == "prepaid"
    assert row["membership"]["rules_override"][0]["cost_usd"] == 2

    cleared = env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={"plan_id": None, "rules_override": None, "credit_mode_override": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["membership"]["plan_id"] is None
    assert cleared.json()["membership"]["rules_override"] is None

    cancel = env["client"].post(
        f"/api/v2/members/{env['member'].id}/membership/cancel",
        headers=_admin(env),
    )
    assert cancel.status_code == 200
    assert cancel.json()["membership"] is None

    s = env["sf"]()
    assert active_membership(s, env["member"].id) is None
    from pulse.proxy.credit import balance_cents

    assert balance_cents(s, env["member"].id) >= 0
    s.close()


def test_refund_rules(env):
    s = env["sf"]()
    open_membership(
        s,
        member_id=env["member"].id,
        plan_id=None,
        created_by_member_id=None,
        credit_mode_override="prepaid",
    )
    grant(s, env["member"].id, 500, note="g")
    usage = ProxyKeyUsage(
        member_id=env["member"].id,
        model="composer-1",
        cost_cents=100,
        usage_cap_pool="auto",
        total_tokens=1,
        ts=NOW,
    )
    s.add(usage)
    s.flush()
    charge = charge_usage(s, env["member"].id, usage)
    adjust = CreditTransaction(
        member_id=env["member"].id,
        kind="adjust",
        amount_cents=-10,
        balance_after_cents=390,
        note="x",
    )
    s.add(adjust)
    s.commit()
    charge_id = charge.id
    adjust_id = adjust.id
    s.close()

    bad = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/transactions/{adjust_id}/refund",
        headers=_admin(env),
        json={},
    )
    assert bad.status_code == 400

    ok = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/transactions/{charge_id}/refund",
        headers=_admin(env),
        json={"note": "退"},
    )
    assert ok.status_code == 201

    twice = env["client"].post(
        f"/api/v2/members/{env['member'].id}/credit/transactions/{charge_id}/refund",
        headers=_admin(env),
        json={},
    )
    assert twice.status_code == 400


def test_statement_join_and_paging(env):
    s = env["sf"]()
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="My Key", member_id=env["member"].id, mode="quota")
    s.add(key)
    s.flush()
    usage = ProxyKeyUsage(
        proxy_key_id=key.id,
        member_id=env["member"].id,
        model="composer-1",
        client="cli",
        usage_cap_pool="auto",
        tokens_input=10,
        tokens_output=5,
        cost_cents=50,
        total_tokens=15,
        ts=NOW,
    )
    s.add(usage)
    s.flush()
    open_membership(
        s,
        member_id=env["member"].id,
        plan_id=None,
        created_by_member_id=None,
        credit_mode_override="prepaid",
    )
    grant(s, env["member"].id, 1000, note="g")
    charge_usage(s, env["member"].id, usage)
    s.commit()
    s.close()

    resp = env["client"].get(
        f"/api/v2/members/{env['member'].id}/credit/transactions",
        headers=_admin(env),
        params={"limit": 1},
    )
    assert resp.status_code == 200
    page = resp.json()
    assert page["next_cursor"] is not None
    charge_row = next(item for item in page["items"] if item["kind"] == "charge")
    assert charge_row["usage"]["model"] == "composer-1"
    assert charge_row["usage"]["client"] == "cli"
    assert charge_row["usage"]["source"]["label"] == "My Key"
    assert "credential" not in json.dumps(charge_row).lower()
    assert "source_account" not in json.dumps(charge_row)


def test_member_sees_own_statement_only(env):
    s = env["sf"]()
    grant(s, env["member"].id, 100, note="m")
    grant(s, env["owner"].id, 200, note="o")
    s.commit()
    s.close()

    mine = env["client"].get("/api/v2/me/credit/transactions", headers=_member(env))
    assert mine.status_code == 200
    assert all(item["kind"] == "grant" for item in mine.json()["items"])
    assert mine.json()["balance_cents"] == 100

    forbidden = env["client"].get(
        f"/api/v2/members/{env['owner'].id}/credit/transactions",
        headers=_member(env),
    )
    assert forbidden.status_code == 403


def test_summary_matches_charges(env):
    s = env["sf"]()
    open_membership(
        s,
        member_id=env["member"].id,
        plan_id=None,
        created_by_member_id=None,
        credit_mode_override="prepaid",
    )
    grant(s, env["member"].id, 1000, note="g")
    usage = ProxyKeyUsage(
        member_id=env["member"].id,
        model="gpt-4",
        cost_cents=120,
        usage_cap_pool="api",
        total_tokens=1,
        ts=datetime.now(UTC),
    )
    s.add(usage)
    s.flush()
    charge_usage(s, env["member"].id, usage)
    s.commit()
    s.close()

    now = datetime.now(UTC)
    start = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    end = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    summary = env["client"].get(
        f"/api/v2/members/{env['member'].id}/credit/summary",
        headers=_admin(env),
        params={"from": start, "to": end},
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["total_charge_cents"] == 120
    assert body["by_model"][0]["model"] == "gpt-4"


def test_csv_matches_list(env):
    s = env["sf"]()
    grant(s, env["member"].id, 300, note="csv")
    s.commit()
    s.close()

    listed = (
        env["client"]
        .get(
            f"/api/v2/members/{env['member'].id}/credit/transactions",
            headers=_admin(env),
        )
        .json()["items"]
    )
    csv_resp = env["client"].get(
        f"/api/v2/members/{env['member'].id}/credit/transactions.csv",
        headers=_admin(env),
    )
    assert csv_resp.status_code == 200
    assert csv_resp.text.startswith("\ufeff")
    lines = csv_resp.text.strip().splitlines()
    assert len(lines) == len(listed) + 1


def test_settings_membership_round_trip(env):
    get_resp = env["client"].get("/api/settings", headers=_admin(env))
    assert get_resp.json()["membership"]["membership_required"] is False

    patch = env["client"].patch(
        "/api/settings/membership",
        headers=_admin(env),
        json={"data": {"membership_required": True}},
    )
    assert patch.status_code == 200
    assert patch.json()["membership"]["membership_required"] is True

    s = env["sf"]()
    assert membership_required_for_team(s, env["team"].id) is True
    s.close()


def test_deprecation_usage_cap_patch(env):
    s = env["sf"]()
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        borrower_member_id=env["member"].id,
        routing_mode="pool",
    )
    s.add(loan)
    s.commit()
    loan_id = loan.id
    s.close()

    resp = env["client"].patch(
        f"/api/v2/loans/{loan_id}/usage-cap",
        headers=_admin(env),
        json={"usage_caps": [{"period": "week", "pool": "auto", "cost_usd": 1}]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == MIGRATED_MSG

    ok = env["client"].patch(
        f"/api/v2/loans/{loan_id}/usage-cap",
        headers=_admin(env),
        json={"usage_caps": []},
    )
    assert ok.status_code == 200
    assert ok.json()["usage_caps"] == []


def test_proxy_key_window_deprecation(env):
    resp = env["client"].post(
        "/api/v2/proxy-keys",
        headers=_admin(env),
        json={"member_id": env["owner"].id, "window_5h_cost_usd": 10},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == MIGRATED_MSG


def test_pkcp_window_still_accepted(env):
    resp = env["client"].post(
        "/api/v2/proxy-keys",
        headers=_admin(env),
        json={"member_id": env["owner"].id, "coding_plan_vendor": "glm", "window_5h_cost_usd": 10},
    )
    assert resp.status_code == 200
    assert resp.json()["window_5h_cost_usd"] == 10


def test_loan_row_shows_member_snapshots(env):
    s = env["sf"]()
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        borrower_member_id=env["member"].id,
        routing_mode="pool",
        alias_key_hint="pka_hint",
        usage_cap_rules=[{"period": "week", "pool": "auto", "limit_cents": 9999}],
    )
    s.add(loan)
    s.flush()
    open_membership(
        s,
        member_id=env["member"].id,
        plan_id=None,
        created_by_member_id=None,
        rules_override=[{"period": "week", "pool": "auto", "limit_cents": 500}],
        credit_mode_override="prepaid",
    )
    grant(s, env["member"].id, 800, note="g")
    s.commit()
    s.close()

    resp = env["client"].get("/api/v2/loans/mine", headers=_member(env))
    assert resp.status_code == 200
    row = resp.json()["items"][0]
    assert row["usage_caps"][0]["limit_cents"] == 500
    assert row["membership"]["credit_mode"] == "prepaid"
    assert row["membership"]["balance_cents"] == 800


def test_archived_plan_assignment_rejected(env):
    plan_resp = env["client"].post(
        "/api/v2/membership-plans",
        headers=_admin(env),
        json={"name": "归档", "rules": [], "credit_mode": "unlimited"},
    )
    plan_id = plan_resp.json()["id"]
    env["client"].patch(
        f"/api/v2/membership-plans/{plan_id}",
        headers=_admin(env),
        json={"status": "archived"},
    )
    resp = env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={"plan_id": plan_id, "rules_override": None, "credit_mode_override": None},
    )
    assert resp.status_code == 400


def test_put_same_archived_plan_allows_override_edit(env):
    active = (
        env["client"]
        .post(
            "/api/v2/membership-plans",
            headers=_admin(env),
            json={"name": "将归档", "rules": [], "credit_mode": "unlimited"},
        )
        .json()
    )
    plan_id = active["id"]
    env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={"plan_id": plan_id, "rules_override": None, "credit_mode_override": None},
    )
    env["client"].patch(
        f"/api/v2/membership-plans/{plan_id}",
        headers=_admin(env),
        json={"status": "archived"},
    )
    resp = env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={"plan_id": plan_id, "rules_override": None, "credit_mode_override": "prepaid"},
    )
    assert resp.status_code == 200
    assert resp.json()["membership"]["credit_mode_override"] == "prepaid"
    assert resp.json()["membership"]["plan_status"] == "archived"


def test_put_switch_to_other_archived_plan_rejected(env):
    first = (
        env["client"]
        .post(
            "/api/v2/membership-plans",
            headers=_admin(env),
            json={"name": "活跃", "rules": [], "credit_mode": "unlimited"},
        )
        .json()["id"]
    )
    archived = (
        env["client"]
        .post(
            "/api/v2/membership-plans",
            headers=_admin(env),
            json={"name": "另一归档", "rules": [], "credit_mode": "unlimited"},
        )
        .json()["id"]
    )
    env["client"].patch(
        f"/api/v2/membership-plans/{archived}",
        headers=_admin(env),
        json={"status": "archived"},
    )
    env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={"plan_id": first, "rules_override": None, "credit_mode_override": None},
    )
    resp = env["client"].put(
        f"/api/v2/members/{env['member'].id}/membership",
        headers=_admin(env),
        json={"plan_id": archived, "rules_override": None, "credit_mode_override": None},
    )
    assert resp.status_code == 400


def test_opening_credit_zero_stores_null(env):
    resp = env["client"].post(
        "/api/v2/membership-plans",
        headers=_admin(env),
        json={"name": "零赠送", "rules": [], "credit_mode": "unlimited", "opening_credit_usd": 0},
    )
    assert resp.status_code == 201
    assert resp.json()["opening_credit_cents"] is None

    plan_id = resp.json()["id"]
    patched = env["client"].patch(
        f"/api/v2/membership-plans/{plan_id}",
        headers=_admin(env),
        json={"opening_credit_usd": 0},
    )
    assert patched.status_code == 200
    assert patched.json()["opening_credit_cents"] is None

    bad = env["client"].post(
        "/api/v2/membership-plans",
        headers=_admin(env),
        json={"name": "负赠送", "rules": [], "credit_mode": "unlimited", "opening_credit_usd": -1},
    )
    assert bad.status_code == 400


def _listed_ids(env, scope: str) -> set[str]:
    resp = env["client"].get("/api/v2/memberships", headers=_admin(env), params={"scope": scope})
    assert resp.status_code == 200
    return {row["member_id"] for row in resp.json()["items"]}


def test_list_scope_keeps_members_with_history(env):
    s = env["sf"]()
    cancel_membership(s, open_membership(s, member_id=env["member"].id, plan_id=None, created_by_member_id=None))
    s.commit()
    s.close()

    member_id = env["member"].id
    owner_id = env["owner"].id
    assert member_id in _listed_ids(env, "managed")
    assert member_id in _listed_ids(env, "lapsed")
    assert member_id not in _listed_ids(env, "active")
    assert member_id in _listed_ids(env, "candidates")
    assert owner_id not in _listed_ids(env, "managed")

    s = env["sf"]()
    grant(s, owner_id, 100, note="g")
    adjust_txn = CreditTransaction(
        member_id=owner_id, kind="adjust", amount_cents=-100, balance_after_cents=0, note="清零"
    )
    s.add(adjust_txn)
    s.commit()
    s.close()
    assert owner_id in _listed_ids(env, "lapsed")

    s = env["sf"]()
    open_membership(s, member_id=member_id, plan_id=None, created_by_member_id=None)
    s.commit()
    s.close()
    assert member_id in _listed_ids(env, "active")
    assert member_id not in _listed_ids(env, "lapsed")
    assert member_id not in _listed_ids(env, "candidates")


def test_foreign_member_returns_404(env):
    resp = env["client"].get(
        f"/api/v2/members/{env['outsider'].id}/credit/transactions",
        headers=_admin(env),
    )
    assert resp.status_code == 404
