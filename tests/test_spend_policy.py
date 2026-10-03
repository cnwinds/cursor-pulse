from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pulse.proxy import service as proxy_service
from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.proxy.membership import open_membership
from pulse.proxy.spend_policy import (
    SpendRule,
    check_spend_rules,
    loan_usage_cap_pool,
    usage_resets_at,
)
from pulse.storage.models import KeyLoan, ProxyKey, ProxyKeyUsage
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from tests.conftest import make_team_repo, make_test_session_factory

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _usage(
    *,
    member_id: str,
    model: str,
    cents: int,
    ts: datetime,
    pool: str | None = None,
    proxy_key_id: str | None = None,
    loan_id: str | None = None,
) -> ProxyKeyUsage:
    return ProxyKeyUsage(
        member_id=member_id,
        proxy_key_id=proxy_key_id,
        loan_id=loan_id,
        model=model,
        cost_cents=cents,
        ts=ts,
        usage_cap_pool=pool,
        total_tokens=1,
    )


@pytest.fixture
def spend_session():
    sf = make_test_session_factory()
    s = sf()
    team, repo = make_team_repo(s)
    member = repo.add_member("spend-m", "Spend")
    member.portal_status = "active"
    s.flush()
    yield s, member
    s.close()


def test_window_boundary_and_total_pool(spend_session):
    s, member = spend_session
    rules = [SpendRule("week", "total", 1000)]
    open_membership(
        s,
        member_id=member.id,
        plan_id=None,
        created_by_member_id=None,
        rules_override=[r.as_dict() for r in rules],
        credit_mode_override="unlimited",
    )
    outside = NOW - timedelta(days=8)
    inside = NOW - timedelta(days=2)
    s.add_all(
        [
            _usage(member_id=member.id, model="composer-1", cents=400, ts=outside, pool="auto"),
            _usage(member_id=member.id, model="claude-opus-4", cents=600, ts=inside, pool="api"),
            _usage(member_id=member.id, model="composer-1", cents=500, ts=inside, pool="auto"),
        ]
    )
    s.flush()
    result = check_spend_rules(s, rules, "composer-1", member_id=member.id, now=NOW)
    assert result["status"] == "limited"
    assert result["pool"] == "total"
    later = inside + timedelta(days=7, minutes=1)
    assert check_spend_rules(s, rules, "composer-1", member_id=member.id, now=later)["status"] == "ok"


def test_or_rules_and_byok(spend_session):
    s, member = spend_session
    rules = [
        SpendRule("5h", "auto", 1000),
        SpendRule("week", "auto", 5000),
    ]
    open_membership(
        s,
        member_id=member.id,
        plan_id=None,
        created_by_member_id=None,
        rules_override=[r.as_dict() for r in rules],
    )
    s.add(_usage(member_id=member.id, model="composer-1", cents=1000, ts=NOW - timedelta(hours=1), pool="auto"))
    s.flush()
    assert check_spend_rules(s, rules, "composer-1", member_id=member.id, now=NOW)["status"] == "limited"
    assert check_spend_rules(s, rules, "GLM-5.2", member_id=member.id, now=NOW)["reason"] == "not_counted"


def test_member_scope_merges_pk_and_loan(spend_session):
    s, member = spend_session
    rules = [SpendRule("5h", "auto", 1000)]
    open_membership(
        s,
        member_id=member.id,
        plan_id=None,
        created_by_member_id=None,
        rules_override=[r.as_dict() for r in rules],
    )
    plain, key_hash, hint = generate_proxy_key()
    key = ProxyKey(
        key_hash=key_hash,
        key_hint=hint,
        name="k",
        member_id=member.id,
        mode="quota",
    )
    s.add(key)
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        borrower_member_id=member.id,
        routing_mode="pool",
    )
    s.add(loan)
    s.flush()
    s.add_all(
        [
            _usage(
                member_id=member.id,
                proxy_key_id=key.id,
                model="composer-1",
                cents=600,
                ts=NOW - timedelta(hours=1),
                pool="auto",
            ),
            _usage(
                member_id=member.id,
                loan_id=loan.id,
                model="composer-1",
                cents=500,
                ts=NOW - timedelta(hours=1),
                pool="auto",
            ),
        ]
    )
    s.flush()
    assert check_spend_rules(s, rules, "composer-1", member_id=member.id, now=NOW)["status"] == "limited"


def test_usage_resets_at():
    window = timedelta(days=7)
    t0 = NOW - timedelta(days=6)
    t1 = NOW - timedelta(days=5)
    events = [(t0, 400), (t1, 600)]
    assert usage_resets_at(events, 500, window) == t1 + window


def test_record_usages_sets_member_client_pool(spend_session):
    s, member = spend_session
    plain, key_hash, hint = generate_proxy_key()
    key = ProxyKey(key_hash=key_hash, key_hint=hint, name="k", member_id=member.id, mode="quota")
    s.add(key)
    s.flush()
    proxy_service.record_usages(
        s,
        [
            {
                "proxy_key_id": key.id,
                "model": "composer-1",
                "client": "ide",
                "tokens": {"input": 1, "output": 0},
            }
        ],
        now=NOW,
    )
    row = s.query(ProxyKeyUsage).filter(ProxyKeyUsage.proxy_key_id == key.id).one()
    assert row.member_id == member.id
    assert row.client == "ide"
    assert row.usage_cap_pool == "auto"
    assert loan_usage_cap_pool("glm-5.2-high") == "api"
