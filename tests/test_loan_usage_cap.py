from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
from pulse.proxy import service as proxy_service
from pulse.proxy.loan_usage_cap import (
    UsageCapConfigError,
    check_loan_usage_cap,
    loan_usage_cap_pool,
    parse_usage_cap_rules,
    usage_cap_enabled,
    usage_resets_at,
)
from pulse.storage.models import KeyLoan, ProxyKeyUsage
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _loan(**kwargs) -> KeyLoan:
    defaults = {
        "delivery_mode": DELIVERY_PROXY_ALIAS,
        "status": "active",
        "usage_cap_rules": [{"period": "week", "pool": "auto", "limit_cents": 1000}],
    }
    defaults.update(kwargs)
    return KeyLoan(**defaults)


def _usage(loan_id: str, *, model: str, cents: int, ts: datetime, pool: str | None = None) -> ProxyKeyUsage:
    return ProxyKeyUsage(
        loan_id=loan_id,
        proxy_key_id=None,
        model=model,
        cost_cents=cents,
        ts=ts,
        usage_cap_pool=pool,
        total_tokens=1,
    )


@pytest.fixture
def cap_session():
    sf = make_test_session_factory()
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    loan = _loan(borrower_member_id=owner.id, routing_mode="pool")
    s.add(loan)
    s.flush()
    yield s, loan
    s.close()


def test_loan_usage_cap_pool_classifier():
    assert loan_usage_cap_pool("") == "auto"
    assert loan_usage_cap_pool("composer-2.5") == "auto"
    assert loan_usage_cap_pool("glm-5.2-high") == "api"
    assert loan_usage_cap_pool("GLM-5.2") is None


def test_usage_resets_at_multi_and_single():
    window = timedelta(days=7)
    t0 = NOW - timedelta(days=6)
    t1 = NOW - timedelta(days=5)
    events = [(t0, 400), (t1, 600)]
    assert usage_resets_at(events, 500, window) == t1 + window
    single = [(NOW - timedelta(hours=1), 1000)]
    assert usage_resets_at(single, 1000, window) == single[0][0] + window


def test_window_boundary_and_release(cap_session):
    s, loan = cap_session
    lid = loan.id
    outside = NOW - timedelta(days=8)
    inside_old = NOW - timedelta(days=6)
    inside_new = NOW - timedelta(hours=1)
    s.add_all(
        [
            _usage(lid, model="composer-1", cents=900, ts=outside, pool="auto"),
            _usage(lid, model="composer-1", cents=500, ts=inside_old, pool="auto"),
            _usage(lid, model="composer-1", cents=500, ts=inside_new, pool="auto"),
        ]
    )
    s.flush()
    assert check_loan_usage_cap(s, lid, "composer-1", now=NOW)["status"] == "limited"
    later = inside_old + timedelta(days=7, minutes=1)
    assert check_loan_usage_cap(s, lid, "composer-1", now=later)["status"] == "ok"


def test_auto_only_cap_blocks_auto_not_api(cap_session):
    s, loan = cap_session
    lid = loan.id
    s.add(_usage(lid, model="composer-1", cents=1000, ts=NOW - timedelta(hours=1), pool="auto"))
    s.flush()
    assert check_loan_usage_cap(s, lid, "claude-opus-4", now=NOW)["status"] == "ok"
    assert check_loan_usage_cap(s, lid, "", now=NOW)["status"] == "limited"
    assert check_loan_usage_cap(s, lid, "composer-2", now=NOW)["status"] == "limited"


def test_byok_not_counted(cap_session):
    s, loan = cap_session
    lid = loan.id
    s.add(_usage(lid, model="composer-1", cents=1000, ts=NOW - timedelta(hours=1), pool="auto"))
    s.flush()
    assert check_loan_usage_cap(s, lid, "GLM-5.2", now=NOW)["status"] == "ok"
    assert check_loan_usage_cap(s, lid, "glm-5.2-high", now=NOW)["status"] == "ok"


def test_loan_pool_aggregates_credentials(cap_session):
    s, loan = cap_session
    lid = loan.id
    s.add_all(
        [
            _usage(lid, model="composer-1", cents=600, ts=NOW - timedelta(hours=2), pool="auto"),
            _usage(lid, model="composer-1", cents=500, ts=NOW - timedelta(hours=1), pool="auto"),
        ]
    )
    s.flush()
    assert check_loan_usage_cap(s, lid, "composer-1", now=NOW)["status"] == "limited"


def test_no_cap_always_ok(cap_session):
    s, loan = cap_session
    loan.usage_cap_rules = []
    s.flush()
    s.add(_usage(loan.id, model="composer-1", cents=99999, ts=NOW, pool="auto"))
    s.flush()
    assert check_loan_usage_cap(s, loan.id, "composer-1", now=NOW)["reason"] == "cap_disabled"


def test_record_usages_sets_usage_cap_pool(cap_session):
    s, loan = cap_session
    proxy_service.record_usages(
        s,
        [
            {
                "loan_id": loan.id,
                "model": "glm-5.2-high",
                "tokens": {"input": 1, "output": 0},
            }
        ],
        now=NOW,
    )
    row = s.query(ProxyKeyUsage).filter(ProxyKeyUsage.loan_id == loan.id).one()
    assert row.usage_cap_pool == "api"


def test_parse_usage_cap_validation():
    with pytest.raises(UsageCapConfigError):
        parse_usage_cap_rules([{"period": "year", "pool": "auto", "cost_usd": 10}])
    with pytest.raises(UsageCapConfigError):
        parse_usage_cap_rules(
            [
                {"period": "week", "pool": "auto", "cost_usd": 10},
                {"period": "week", "pool": "auto", "cost_usd": 20},
            ]
        )
    parsed = parse_usage_cap_rules(
        [
            {"period": "5h", "pool": "auto", "cost_usd": 10},
            {"period": "week", "pool": "auto", "cost_usd": 50},
        ]
    )
    assert [rule.period for rule in parsed] == ["5h", "week"]
    assert parsed[0].limit_cents == 1000


def test_or_rules_either_window_blocks(cap_session):
    s, loan = cap_session
    loan.usage_cap_rules = [
        {"period": "5h", "pool": "auto", "limit_cents": 1000},
        {"period": "week", "pool": "auto", "limit_cents": 5000},
    ]
    s.add(_usage(loan.id, model="composer-1", cents=1000, ts=NOW - timedelta(hours=1), pool="auto"))
    s.flush()
    assert check_loan_usage_cap(s, loan.id, "composer-1", now=NOW)["status"] == "limited"
    later = NOW + timedelta(hours=5)
    assert check_loan_usage_cap(s, loan.id, "composer-1", now=later)["status"] == "ok"


@pytest.fixture
def api_client():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="t", name="T"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="tok"),
    )
    client, proxy = make_module_web_client(config)
    sf = make_test_session_factory()
    proxy.bind(sf)
    return client, sf, config


def test_internal_loan_usage_cap_endpoint(api_client):
    client, sf, _config = api_client
    s = sf()
    loan = _loan()
    s.add(loan)
    s.flush()
    s.add(_usage(loan.id, model="composer-1", cents=1000, ts=NOW - timedelta(hours=1), pool="auto"))
    s.commit()
    assert check_loan_usage_cap(s, loan.id, "composer-1", now=NOW)["status"] == "limited"
    resp = client.post(
        "/api/internal/v1/proxy/loan-usage-cap",
        json={"loan_id": loan.id, "model": "composer-1"},
        headers={"X-Pulse-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "limited"
    assert body["reason"] == "loan_usage_cap_exceeded"
    assert "【小脉借用】" in body["message"]


def test_patch_usage_cap_and_clear(api_client):
    client, sf, config = api_client
    s = sf()
    team, repo = make_team_repo(s, slug="t")
    owner = bootstrap_portal_owner(repo, channel_user_id="adm", display_name="A", password="pw")
    loan = _loan(
        usage_cap_period=None,
        auto_cost_limit_cents=None,
        api_cost_limit_cents=None,
        borrower_member_id=owner.id,
        routing_mode="pool",
    )
    s.add(loan)
    s.commit()

    token = create_access_token(config, owner)

    bad = client.patch(
        f"/api/v2/loans/{loan.id}/usage-cap",
        json={
            "clear": False,
            "usage_caps": [
                {"period": "week", "pool": "auto", "cost_usd": 10},
                {"period": "week", "pool": "auto", "cost_usd": 20},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert bad.status_code == 400

    ok = client.patch(
        f"/api/v2/loans/{loan.id}/usage-cap",
        json={
            "clear": False,
            "usage_caps": [
                {"period": "5h", "pool": "auto", "cost_usd": 10},
                {"period": "week", "pool": "api", "cost_usd": 50},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200
    data = ok.json()
    assert len(data["usage_caps"]) == 2
    assert data["usage_caps"][0]["cost_usd"] == 10

    cleared = client.patch(
        f"/api/v2/loans/{loan.id}/usage-cap",
        json={"clear": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["usage_caps"] == []


def test_reassign_same_pool_saves_usage_caps(api_client):
    client, sf, config = api_client
    s = sf()
    _team, repo = make_team_repo(s, slug="t")
    owner = bootstrap_portal_owner(repo, channel_user_id="adm3", display_name="C", password="pw")
    loan = _loan(borrower_member_id=owner.id, routing_mode="pool")
    s.add(loan)
    s.commit()
    headers = {"Authorization": f"Bearer {create_access_token(config, owner)}"}

    unchanged = client.post(
        f"/api/v2/loans/{loan.id}/reassign-source",
        json={"lender_mode": "auto"},
        headers=headers,
    )
    assert unchanged.status_code == 400

    duplicate = client.post(
        f"/api/v2/loans/{loan.id}/reassign-source",
        json={
            "lender_mode": "auto",
            "usage_caps": [
                {"period": "week", "pool": "auto", "cost_usd": 10},
                {"period": "week", "pool": "auto", "cost_usd": 50},
            ],
        },
        headers=headers,
    )
    assert duplicate.status_code == 400

    saved = client.post(
        f"/api/v2/loans/{loan.id}/reassign-source",
        json={
            "lender_mode": "auto",
            "usage_caps": [
                {"period": "5h", "pool": "auto", "cost_usd": 10},
                {"period": "week", "pool": "auto", "cost_usd": 50},
            ],
        },
        headers=headers,
    )
    assert saved.status_code == 200
    assert [(row["period"], row["pool"], row["cost_usd"]) for row in saved.json()["usage_caps"]] == [
        ("5h", "auto", 10),
        ("week", "auto", 50),
    ]

    cleared = client.post(
        f"/api/v2/loans/{loan.id}/reassign-source",
        json={"lender_mode": "auto", "usage_caps": []},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["usage_caps"] == []


def test_cursor_direct_rejects_cap_patch(api_client):
    client, sf, config = api_client
    s = sf()
    team, repo = make_team_repo(s, slug="t")
    owner = bootstrap_portal_owner(repo, channel_user_id="adm2", display_name="B", password="pw")
    loan = KeyLoan(
        delivery_mode="cursor_direct",
        status="active",
        borrower_member_id=owner.id,
        routing_mode="pool",
    )
    s.add(loan)
    s.commit()

    token = create_access_token(config, owner)
    resp = client.patch(
        f"/api/v2/loans/{loan.id}/usage-cap",
        json={"clear": False, "usage_caps": [{"period": "week", "pool": "auto", "cost_usd": 1}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_passthrough_loan_not_applicable(cap_session):
    s, _ = cap_session
    loan = KeyLoan(
        delivery_mode="cursor_direct",
        status="active",
        usage_cap_rules=[{"period": "week", "pool": "auto", "limit_cents": 100}],
    )
    s.add(loan)
    s.flush()
    assert check_loan_usage_cap(s, loan.id, "composer-1", now=NOW)["reason"] == "not_applicable"


def test_usage_cap_enabled():
    assert not usage_cap_enabled(_loan(usage_cap_rules=[]))
    assert usage_cap_enabled(_loan())


def test_legacy_columns_still_apply(cap_session):
    s, loan = cap_session
    loan.usage_cap_rules = None
    loan.usage_cap_period = "5h"
    loan.auto_cost_limit_cents = 1000
    loan.api_cost_limit_cents = None
    s.add(_usage(loan.id, model="composer-1", cents=1000, ts=NOW - timedelta(minutes=30), pool="auto"))
    s.flush()
    assert check_loan_usage_cap(s, loan.id, "composer-1", now=NOW)["status"] == "limited"
