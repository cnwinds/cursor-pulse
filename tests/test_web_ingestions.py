from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from decimal import Decimal

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.domain import CostRaw, ParseSummary, ParsedCsv, UsageEventRecord
from pulse.storage.models import Base, UsageDailyAggregate
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def ingest_env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
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
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")
    member = repo.add_member("u1", "Alice")
    seed_v2_catalog(s, team)
    s.flush()

    tool_repo = ToolCenterRepository(s, team.id)
    zhipu = tool_repo.get_vendor_by_slug("zhipu")
    plan = next(p for p in tool_repo.list_plans(zhipu.id) if p.slug == "glm_coding_lite")
    account = tool_repo.create_account(
        vendor_id=zhipu.id,
        plan_id=plan.id,
        account_identifier="zhipu@test.com",
        status="trial",
        primary_member_id=member.id,
    )
    rec = UsageEventRecord(
        event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        event_date=date(2026, 6, 1),
        kind="usage",
        model="glm-4",
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=100,
        tokens_cache_read=0,
        tokens_output=50,
        tokens_total=150,
        cost_raw=CostRaw.USAGE_BASED,
        cost_usd=Decimal("1.5"),
        cloud_agent_id=None,
        automation_id=None,
        source_row_hash="abc",
    )
    parsed = ParsedCsv(
        records=[rec],
        summary=ParseSummary(
            period_hint="2026-06",
            date_min=date(2026, 6, 1),
            date_max=date(2026, 6, 1),
            event_count=1,
            total_tokens=150,
            total_cost_usd=Decimal("1.5"),
            top_models=[],
            all_included_or_free=False,
        ),
    )
    pending = repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="dingtalk",
        input_type="screenshot",
        status="pending_review",
        account_id=account.id,
    )
    s.add(
        UsageDailyAggregate(
            account_id=account.id,
            event_date=date(2026, 6, 1),
            model="glm-4",
            event_count=1,
            total_cost_usd=1.5,
            tokens_input=100,
            tokens_output=50,
            tokens_cache_read=0,
        )
    )
    repo.commit()
    s.close()

    client = TestClient(create_app(config, sf))
    return {
        "client": client,
        "config": config,
        "owner": owner,
        "pending_id": pending.id,
        "account_id": account.id,
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_list_ingestions_filter(ingest_env):
    client = ingest_env["client"]
    token = create_access_token(ingest_env["config"], ingest_env["owner"])

    res = client.get(
        "/api/v2/ingestions",
        headers=_headers(token),
        params={"period": "2026-06", "status": "pending_review"},
    )
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["id"] == ingest_env["pending_id"]
    assert rows[0]["source_type"] == "manual_vision"


def test_list_ingestions_submission_alias(ingest_env):
    client = ingest_env["client"]
    token = create_access_token(ingest_env["config"], ingest_env["owner"])
    res = client.get("/api/v2/submissions", headers=_headers(token), params={"period": "2026-06"})
    assert res.status_code == 200
    assert len(res.json()) >= 1


def test_confirm_ingestion_via_api(ingest_env):
    client = ingest_env["client"]
    token = create_access_token(ingest_env["config"], ingest_env["owner"])
    pending_id = ingest_env["pending_id"]

    res = client.post(
        f"/api/v2/ingestions/{pending_id}/confirm",
        headers=_headers(token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"


def test_daily_usage_endpoint(ingest_env):
    client = ingest_env["client"]
    token = create_access_token(ingest_env["config"], ingest_env["owner"])
    account_id = ingest_env["account_id"]

    res = client.get(
        f"/api/v2/accounts/{account_id}/usage/daily",
        headers=_headers(token),
        params={"start": "2026-06-01", "end": "2026-06-30"},
    )
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["model"] == "glm-4"
    assert rows[0]["event_count"] == 1
