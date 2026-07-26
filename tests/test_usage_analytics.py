from __future__ import annotations

import base64
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.storage.models import Base, UsageDailyAggregate
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage_analytics import pool_for_model
from pulse.tool_center.usage import model_family
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def analytics_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
    member = repo.add_member("alice", "Alice")
    member.portal_role = "ai_member"
    member.portal_status = "active"
    seed_v2_catalog(s, team)
    s.flush()

    tool_repo = ToolCenterRepository(s, team.id)
    cursor_account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(
        cursor_account.id,
        primary_member_id=member.id,
        account_identifier="alice@example.com",
    )

    # Second cursor account for filter tests
    plans = tool_repo.list_plans(vendor_id=cursor_account.vendor_id)
    plan = plans[0]
    other = tool_repo.create_account(
        vendor_id=cursor_account.vendor_id,
        plan_id=plan.id,
        account_identifier="bob@example.com",
        ownership="company",
        status="shared",
    )

    rows = [
        UsageDailyAggregate(
            account_id=cursor_account.id,
            event_date=date(2026, 7, 1),
            model="claude-4-sonnet",
            event_count=3,
            total_cost_usd=1.5,
            tokens_input=1000,
            tokens_output=500,
            tokens_cache_read=200,
        ),
        UsageDailyAggregate(
            account_id=cursor_account.id,
            event_date=date(2026, 7, 2),
            model="composer-1",
            event_count=2,
            total_cost_usd=0.4,
            tokens_input=400,
            tokens_output=100,
            tokens_cache_read=0,
        ),
        UsageDailyAggregate(
            account_id=other.id,
            event_date=date(2026, 7, 1),
            model="glm-4.5",
            event_count=1,
            total_cost_usd=0.2,
            tokens_input=300,
            tokens_output=50,
            tokens_cache_read=50,
        ),
        UsageDailyAggregate(
            account_id=cursor_account.id,
            event_date=date(2026, 6, 30),
            model="claude-4-sonnet",
            event_count=9,
            total_cost_usd=9.0,
            tokens_input=9000,
            tokens_output=9000,
            tokens_cache_read=0,
        ),
    ]
    s.add_all(rows)
    repo.commit()
    s.close()

    client = TestClient(create_app(config, sf))
    return {
        "client": client,
        "config": config,
        "owner": owner,
        "member": member,
        "cursor_account": cursor_account,
        "other_account": other,
        "session_factory": sf,
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_pool_for_model_mapping():
    assert pool_for_model("composer-1") == "auto_composer"
    assert pool_for_model("auto") == "auto_composer"
    assert pool_for_model("cursor-grok-4.5-high") == "auto_composer"
    assert pool_for_model("claude-4-sonnet") == "api"
    assert pool_for_model("gpt-5") == "api"
    assert pool_for_model("glm-4.5") == "third_party"
    assert pool_for_model("minimax-m1") == "third_party"


def test_model_family_mapping_edges():
    assert model_family("claude-4-sonnet") == "Claude"
    assert model_family("gpt-5.1") == "GPT"
    assert model_family("o3-mini") == "GPT"
    assert model_family("gemini-2.5-pro") == "Gemini"
    assert model_family("glm-4.5") == "GLM"
    assert model_family("minimax-m1") == "MiniMax"
    assert model_family("composer-1") == "Other"
    assert model_family("cursor-grok-4.5") == "Other"


def test_overview_kpi_and_series(analytics_env):
    client = analytics_env["client"]
    token = create_access_token(analytics_env["config"], analytics_env["owner"])
    res = client.get(
        "/api/v2/usage-analytics/overview",
        params={"start": "2026-07-01", "end": "2026-07-02"},
        headers=_headers(token),
    )
    assert res.status_code == 200
    data = res.json()
    # July rows only (June excluded): tokens = 1000+500+200 + 400+100+0 + 300+50+50 = 2600
    assert data["kpi"]["tokens_total"] == 2600
    assert data["kpi"]["tokens_input"] == 1700
    assert data["kpi"]["tokens_output"] == 650
    assert data["kpi"]["tokens_cache_read"] == 250
    assert data["kpi"]["event_count"] == 6
    assert data["kpi"]["cost_usd"] == pytest.approx(2.1)
    assert data["timezone"] == "Asia/Shanghai"

    assert len(data["series_by_day"]) == 2
    assert data["series_by_day"][0]["date"] == "2026-07-01"
    assert data["series_by_day"][0]["tokens_total"] == 2100  # 1700 + 400 from two accounts
    assert data["series_by_day"][1]["date"] == "2026-07-02"
    assert data["series_by_day"][1]["tokens_total"] == 500

    by_account = {row["account_id"]: row for row in data["by_account"]}
    alice = analytics_env["cursor_account"].id
    bob = analytics_env["other_account"].id
    assert by_account[alice]["tokens_total"] == 2200
    assert by_account[alice]["primary_member_name"] == "Alice"
    assert by_account[bob]["tokens_total"] == 400

    pools = {row["pool"]: row for row in data["by_pool"]}
    assert pools["api"]["tokens_total"] == 1700
    assert pools["auto_composer"]["tokens_total"] == 500
    assert pools["third_party"]["tokens_total"] == 400

    families = {row["family"]: row for row in data["by_family"]}
    assert families["Claude"]["tokens_total"] == 1700
    assert families["GLM"]["tokens_total"] == 400
    assert families["Other"]["tokens_total"] == 500


def test_overview_filters_account_and_member(analytics_env):
    client = analytics_env["client"]
    token = create_access_token(analytics_env["config"], analytics_env["owner"])
    alice_id = analytics_env["cursor_account"].id
    member_id = analytics_env["member"].id

    res = client.get(
        "/api/v2/usage-analytics/overview",
        params={
            "start": "2026-07-01",
            "end": "2026-07-02",
            "account_ids": alice_id,
        },
        headers=_headers(token),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kpi"]["tokens_total"] == 2200
    assert len(data["by_account"]) == 1
    assert data["by_account"][0]["account_id"] == alice_id

    res2 = client.get(
        "/api/v2/usage-analytics/overview",
        params={
            "start": "2026-07-01",
            "end": "2026-07-02",
            "primary_member_ids": member_id,
        },
        headers=_headers(token),
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["kpi"]["tokens_total"] == 2200
    assert all(row["account_id"] == alice_id for row in data2["by_account"])


def test_overview_rejects_long_range(analytics_env):
    client = analytics_env["client"]
    token = create_access_token(analytics_env["config"], analytics_env["owner"])
    res = client.get(
        "/api/v2/usage-analytics/overview",
        params={"start": "2025-01-01", "end": "2026-07-01"},
        headers=_headers(token),
    )
    assert res.status_code == 400
    assert "366" in res.json()["detail"]


def test_daily_breakdown(analytics_env):
    client = analytics_env["client"]
    token = create_access_token(analytics_env["config"], analytics_env["owner"])
    alice_id = analytics_env["cursor_account"].id
    res = client.get(
        "/api/v2/usage-analytics/daily-breakdown",
        params={
            "start": "2026-07-01",
            "end": "2026-07-02",
            "account_id": alice_id,
        },
        headers=_headers(token),
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2
    assert {i["model"] for i in items} == {"claude-4-sonnet", "composer-1"}

    res2 = client.get(
        "/api/v2/usage-analytics/daily-breakdown",
        params={
            "start": "2026-07-01",
            "end": "2026-07-02",
            "model": "glm-4.5",
        },
        headers=_headers(token),
    )
    assert res2.status_code == 200
    items2 = res2.json()["items"]
    assert len(items2) == 1
    assert items2[0]["pool"] == "third_party"
    assert items2[0]["family"] == "GLM"
