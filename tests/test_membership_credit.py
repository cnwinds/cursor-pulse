from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
from pulse.proxy import service as proxy_service
from pulse.proxy.credit import adjust, balance_cents, grant, reconcile, refund_charge
from pulse.proxy.membership import (
    OPENING_CREDIT_NOTE,
    MembershipError,
    active_membership,
    cancel_membership,
    change_membership,
    effective_policy,
    evaluate_spend,
    open_membership,
)
from pulse.proxy.team_membership import set_membership_required
from pulse.storage.models import CreditTransaction, KeyLoan, MembershipPlan, ProxyKey, ProxyKeyUsage
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from sqlalchemy import select
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def mc_env():
    sf = make_test_session_factory()
    s = sf()
    team, repo = make_team_repo(s)
    member = repo.add_member("credit-m", "Credit")
    member.portal_status = "active"
    s.flush()
    yield s, team, member, sf
    s.close()


def test_grant_adjust_refund_and_reconcile(mc_env):
    s, _team, member, _sf = mc_env
    g = grant(s, member.id, 1000, note="充值")
    assert g.balance_after_cents == 1000
    a = adjust(s, member.id, -200, note="纠错")
    assert a.balance_after_cents == 800
    from pulse.proxy.credit import charge_usage

    row = ProxyKeyUsage(
        member_id=member.id,
        model="composer-1",
        cost_cents=100,
        usage_cap_pool="auto",
        total_tokens=1,
        ts=NOW,
    )
    s.add(row)
    s.flush()
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="prepaid")
    txn = charge_usage(s, member.id, row)
    assert txn is not None
    assert txn.balance_after_cents == 700
    refund_charge(s, txn.id, note="退还")
    assert balance_cents(s, member.id) == 800
    with pytest.raises(ValueError):
        refund_charge(s, txn.id, note="重复")
    assert reconcile(s) == []


def test_idempotent_charge_and_negative_balance(mc_env):
    s, _team, member, _sf = mc_env
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="prepaid")
    grant(s, member.id, 50, note="少量")
    row = ProxyKeyUsage(
        member_id=member.id,
        model="composer-1",
        cost_cents=100,
        usage_cap_pool="auto",
        total_tokens=1,
        ts=NOW,
    )
    s.add(row)
    s.flush()
    from pulse.proxy.credit import charge_usage

    t1 = charge_usage(s, member.id, row)
    t2 = charge_usage(s, member.id, row)
    assert t1.id == t2.id
    assert balance_cents(s, member.id) == -50
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="k", member_id=member.id, mode="quota")
    s.add(key)
    s.flush()
    blocked = evaluate_spend(s, proxy_key_id=key.id, model="composer-1", now=NOW)
    assert blocked["status"] == "limited"
    assert blocked["reason"] == "credit_exhausted"
    grant(s, member.id, 100, note="补")
    assert balance_cents(s, member.id) == 50
    assert evaluate_spend(s, proxy_key_id=key.id, model="composer-1", now=NOW)["status"] == "ok"


def test_concurrent_balance_updates(mc_env):
    _s, _team, member, sf = mc_env
    s1 = sf()
    s2 = sf()
    grant(s1, member.id, 100, note="a")
    grant(s2, member.id, 200, note="b")
    s1.commit()
    s2.commit()
    s3 = sf()
    assert balance_cents(s3, member.id) == 300
    s3.close()
    s1.close()
    s2.close()


def test_unlimited_no_charge_and_byok_skip(mc_env):
    s, _team, member, _sf = mc_env
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="unlimited")
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="k", member_id=member.id, mode="quota")
    s.add(key)
    s.flush()
    proxy_service.record_usages(
        s,
        [{"proxy_key_id": key.id, "model": "composer-1", "tokens": {"input": 100, "output": 0}}],
        now=NOW,
    )
    from pulse.storage.models import CreditTransaction

    assert s.query(CreditTransaction).count() == 0
    assert evaluate_spend(s, proxy_key_id=key.id, model="GLM-5.2", now=NOW)["reason"] == "not_counted"


def test_opening_credit_once(mc_env):
    s, team, member, _sf = mc_env
    plan = MembershipPlan(team_id=team.id, name="入门", opening_credit_cents=500, credit_mode="prepaid")
    s.add(plan)
    s.flush()
    open_membership(s, member_id=member.id, plan_id=plan.id, created_by_member_id=member.id)
    assert balance_cents(s, member.id) == 500
    cancel_membership(s, active_membership(s, member.id))
    open_membership(s, member_id=member.id, plan_id=plan.id, created_by_member_id=member.id)
    assert balance_cents(s, member.id) == 500
    from pulse.storage.models import CreditTransaction

    grants = s.query(CreditTransaction).filter(CreditTransaction.note == OPENING_CREDIT_NOTE).all()
    assert len(grants) == 1


def test_reprice_does_not_change_charges(mc_env):
    s, _team, member, _sf = mc_env
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="prepaid")
    grant(s, member.id, 1000, note="充值")
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="k", member_id=member.id, mode="quota")
    s.add(key)
    s.flush()
    proxy_service.record_usages(
        s,
        [{"proxy_key_id": key.id, "model": "composer-1", "tokens": {"input": 1000, "output": 0}}],
        now=NOW,
    )
    before = balance_cents(s, member.id)
    proxy_service.reprice_proxy_usages(s, proxy_key_id=key.id)
    assert balance_cents(s, member.id) == before


def test_effective_policy_override_and_plan_edit(mc_env):
    s, team, member, _sf = mc_env
    plan = MembershipPlan(
        team_id=team.id,
        name="标准",
        rules=[{"period": "week", "pool": "auto", "limit_cents": 1000}],
        credit_mode="unlimited",
    )
    s.add(plan)
    s.flush()
    m = open_membership(s, member_id=member.id, plan_id=plan.id, created_by_member_id=None)
    assert effective_policy(s, m).rules[0].limit_cents == 1000
    change_membership(
        s,
        m,
        rules_override=[{"period": "week", "pool": "auto", "limit_cents": 500}],
    )
    assert effective_policy(s, m).rules[0].limit_cents == 500
    plan.rules = [{"period": "week", "pool": "auto", "limit_cents": 200}]
    s.flush()
    change_membership(s, m, clear_rules_override=True)
    assert effective_policy(s, m).rules[0].limit_cents == 200


def test_membership_required(mc_env):
    s, team, member, _sf = mc_env
    set_membership_required(s, team.id, True)
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="k", member_id=member.id, mode="quota")
    s.add(key)
    s.flush()
    result = evaluate_spend(s, proxy_key_id=key.id, model="composer-1", now=NOW)
    assert result["reason"] == "membership_required"
    set_membership_required(s, team.id, False)
    assert evaluate_spend(s, proxy_key_id=key.id, model="composer-1", now=NOW)["status"] == "ok"


def test_balance_after_continuous_by_id(mc_env):
    s, _team, member, _sf = mc_env
    grant(s, member.id, 1000, note="1")
    grant(s, member.id, 500, note="2")
    adjust(s, member.id, -200, note="adj")
    txns = s.scalars(
        select(CreditTransaction).where(CreditTransaction.member_id == member.id).order_by(CreditTransaction.id)
    ).all()
    running = 0
    for txn in txns:
        running += txn.amount_cents
        assert txn.balance_after_cents == running
    assert txns[1].id > txns[0].id


def test_grant_and_spend_visible_without_commit(mc_env):
    s, _team, member, _sf = mc_env
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="prepaid")
    grant(s, member.id, 500, note="充值")
    assert balance_cents(s, member.id) == 500
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="k", member_id=member.id, mode="quota")
    s.add(key)
    s.flush()
    assert evaluate_spend(s, proxy_key_id=key.id, model="composer-1", now=NOW)["status"] == "ok"


def test_charge_visible_without_commit(mc_env):
    s, _team, member, _sf = mc_env
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="prepaid")
    grant(s, member.id, 100, note="充值")
    from pulse.proxy.credit import charge_usage

    row = ProxyKeyUsage(
        member_id=member.id,
        model="composer-1",
        cost_cents=30,
        usage_cap_pool="auto",
        total_tokens=1,
        ts=NOW,
    )
    s.add(row)
    s.flush()
    charge_usage(s, member.id, row)
    assert balance_cents(s, member.id) == 70


def test_zero_cost_charge_skipped(mc_env):
    s, _team, member, _sf = mc_env
    open_membership(s, member_id=member.id, plan_id=None, created_by_member_id=None, credit_mode_override="prepaid")
    grant(s, member.id, 100, note="充值")
    from pulse.proxy.credit import charge_usage

    row = ProxyKeyUsage(
        member_id=member.id,
        model="composer-1",
        cost_cents=0,
        usage_cap_pool="auto",
        total_tokens=1,
        ts=NOW,
    )
    s.add(row)
    s.flush()
    assert charge_usage(s, member.id, row) is None
    assert s.query(CreditTransaction).filter(CreditTransaction.kind == "charge").count() == 0


def test_open_membership_rejects_archived_plan(mc_env):
    s, team, member, _sf = mc_env
    plan = MembershipPlan(team_id=team.id, name="旧", status="archived")
    s.add(plan)
    s.flush()
    with pytest.raises(MembershipError, match="归档"):
        open_membership(s, member_id=member.id, plan_id=plan.id, created_by_member_id=None)


def test_open_membership_rejects_foreign_team_plan(mc_env):
    s, team, member, _sf = mc_env
    other_team, _other_repo = make_team_repo(s, slug="other-team")
    plan = MembershipPlan(team_id=other_team.id, name="外", status="active")
    s.add(plan)
    s.flush()
    with pytest.raises(MembershipError, match="团队"):
        open_membership(s, member_id=member.id, plan_id=plan.id, created_by_member_id=None)


def test_change_membership_validates_plan_and_rules(mc_env):
    s, team, member, _sf = mc_env
    active = MembershipPlan(team_id=team.id, name="A", status="active")
    archived = MembershipPlan(team_id=team.id, name="Z", status="archived")
    s.add_all([active, archived])
    s.flush()
    m = open_membership(s, member_id=member.id, plan_id=active.id, created_by_member_id=None)
    with pytest.raises(MembershipError, match="归档"):
        change_membership(s, m, plan_id=archived.id)
    with pytest.raises(MembershipError):
        change_membership(s, m, rules_override=[{"period": "year", "pool": "auto", "cost_usd": 1}])
    change_membership(s, m, clear_plan=True)
    assert m.plan_id is None
    change_membership(s, m, rules_override=[], clear_rules_override=False)
    assert m.rules_override == []


def test_change_membership_allows_edit_on_unchanged_archived_plan(mc_env):
    s, team, member, _sf = mc_env
    plan = MembershipPlan(team_id=team.id, name="A", status="active")
    s.add(plan)
    s.flush()
    m = open_membership(s, member_id=member.id, plan_id=plan.id, created_by_member_id=None)
    plan.status = "archived"
    s.flush()
    change_membership(s, m, plan_id=plan.id, credit_mode_override="prepaid")
    assert m.credit_mode_override == "prepaid"


def test_change_membership_rejects_switch_to_archived_plan(mc_env):
    s, team, member, _sf = mc_env
    active = MembershipPlan(team_id=team.id, name="A", status="active")
    archived = MembershipPlan(team_id=team.id, name="Z", status="archived")
    s.add_all([active, archived])
    s.flush()
    m = open_membership(s, member_id=member.id, plan_id=active.id, created_by_member_id=None)
    with pytest.raises(MembershipError, match="归档"):
        change_membership(s, m, plan_id=archived.id)


def test_credit_mode_override_validation(mc_env):
    s, _team, member, _sf = mc_env
    with pytest.raises(MembershipError, match="余额模式"):
        open_membership(
            s,
            member_id=member.id,
            plan_id=None,
            created_by_member_id=None,
            credit_mode_override="bogus",
        )


def test_spend_check_both_ids_and_empty_both():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="t2", name="T2"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="tok2"),
    )
    client, proxy = make_module_web_client(config)
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s, slug="t2")
    member = repo.add_member("sc", "SC")
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(key_hash=kh, key_hint=hint, name="k", member_id=member.id, mode="quota")
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        borrower_member_id=member.id,
    )
    s.add_all([key, loan])
    s.commit()
    headers = {"X-Pulse-Internal-Token": "tok2"}
    ok = client.post(
        "/api/internal/v1/proxy/spend-check",
        json={"proxy_key_id": key.id, "loan_id": loan.id, "model": "composer-1"},
        headers=headers,
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/internal/v1/proxy/spend-check",
        json={"proxy_key_id": "", "loan_id": "", "model": "composer-1"},
        headers=headers,
    )
    assert bad.status_code == 400


def test_spend_check_orphan_loan_rules():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="t", name="T"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="tok"),
    )
    client, proxy = make_module_web_client(config)
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        usage_cap_rules=[{"period": "week", "pool": "auto", "limit_cents": 1000}],
    )
    s.add(loan)
    s.flush()
    s.add(
        ProxyKeyUsage(
            loan_id=loan.id,
            model="composer-1",
            cost_cents=1000,
            ts=NOW - timedelta(hours=1),
            usage_cap_pool="auto",
            total_tokens=1,
        )
    )
    s.commit()
    body = client.post(
        "/api/internal/v1/proxy/spend-check",
        json={"loan_id": loan.id, "model": "composer-1"},
        headers={"X-Pulse-Internal-Token": "tok"},
    ).json()
    assert body["status"] == "limited"
    assert body["reason"] == "spend_rule_exceeded"
