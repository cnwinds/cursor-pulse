from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from decimal import Decimal

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.domain import CostRaw, ParseSummary, ParsedCsv, UsageEventRecord
from pulse.storage.models import AiAccountMember, Base, Member, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.submission_status import build_submission_status_payload
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


def _parsed_with_cost(total: float, period: str = "2026-06", date_max: date | None = None) -> ParsedCsv:
    end = date_max or date(2026, 6, 30)
    rec = UsageEventRecord(
        event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        event_date=date(2026, 6, 1),
        kind="usage",
        model="claude-sonnet-4",
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=0,
        tokens_cache_read=0,
        tokens_output=0,
        tokens_total=0,
        cost_raw=CostRaw.USAGE_BASED,
        cost_usd=Decimal(str(total)),
        cloud_agent_id=None,
        automation_id=None,
        source_row_hash="abc",
    )
    summary = ParseSummary(
        period_hint=period,
        date_min=date(2026, 6, 1),
        date_max=end,
        event_count=1,
        total_tokens=0,
        total_cost_usd=Decimal(str(total)),
        top_models=[],
        all_included_or_free=False,
    )
    return ParsedCsv(records=[rec], summary=summary)


@pytest.fixture
def status_env():
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
    member.portal_role = "ai_member"
    member.portal_status = "active"
    seed_v2_catalog(s, team)
    s.flush()

    tool_repo = ToolCenterRepository(s, team.id)
    accounts = tool_repo.list_accounts()
    account = accounts[0]
    secondary_account = accounts[1]
    tool_repo.update_account(account.id, primary_member_id=member.id, status="trial")
    tool_repo.update_account(secondary_account.id, primary_member_id=owner.id, status="shared")
    s.add(
        AiAccountMember(
            account_id=secondary_account.id,
            member_id=member.id,
            role="secondary",
        )
    )
    repo.commit()
    s.close()
    client = TestClient(create_app(config, sf))
    return {
        "client": client,
        "config": config,
        "sf": sf,
        "owner": owner,
        "member": member,
        "account": account,
        "secondary_account": secondary_account,
        "team_id": team.id,
    }


def test_submission_status_admin_sees_all_accounts(status_env):
    client = status_env["client"]
    config = status_env["config"]
    owner = status_env["owner"]
    token = create_access_token(config, owner)
    res = client.get(
        "/api/v2/submission-status",
        params={"period": "2026-06"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["total_accounts"] == 3
    assert body["viewer_scope"] == "all"
    assert len(body["groups"]) >= 2


def test_submission_status_member_sees_primary_and_shared(status_env):
    client = status_env["client"]
    config = status_env["config"]
    sf = status_env["sf"]
    member_id = status_env["member"].id
    account = status_env["account"]
    secondary = status_env["secondary_account"]

    s = sf()
    member = s.get(Member, member_id)
    token = create_access_token(config, member)
    s.close()

    res = client.get(
        "/api/v2/submission-status",
        params={"period": "2026-06"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["viewer_scope"] == "self"
    ids = {row["account_id"] for row in body["accounts"]}
    assert account.id in ids
    assert secondary.id in ids
    assert len(ids) == 2


def test_submission_status_flags_date_mismatch(status_env):
    client = status_env["client"]
    config = status_env["config"]
    sf = status_env["sf"]
    account = status_env["account"]
    member_id = status_env["member"].id
    owner = status_env["owner"]

    s = sf()
    _team, repo = make_team_repo(s)
    member = s.get(Member, member_id)
    parsed = _parsed_with_cost(10.0, date_max=date(2026, 6, 15))
    repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
        account_id=account.id,
    )
    repo.commit()
    s.close()

    token = create_access_token(config, owner)
    res = client.get(
        "/api/v2/submission-status",
        params={"period": "2026-06"},
        headers={"Authorization": f"Bearer {token}"},
    )
    row = next(r for r in res.json()["accounts"] if r["account_id"] == account.id)
    assert row["submission_state"] == "submitted_warning"
    assert any("截止日" in issue for issue in row["issues"])


def test_confirm_pending_creates_usage_summary(status_env):
    client = status_env["client"]
    config = status_env["config"]
    sf = status_env["sf"]
    account = status_env["account"]
    member_id = status_env["member"].id
    owner = status_env["owner"]

    s = sf()
    _team, repo = make_team_repo(s)
    member = s.get(Member, member_id)
    parsed = _parsed_with_cost(20.0)
    pending = repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
        account_id=account.id,
        status="pending_review",
    )
    repo.commit()
    pending_id = pending.id
    s.close()

    token = create_access_token(config, owner)
    res = client.post(
        f"/api/v2/submissions/{pending_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200

    s = sf()
    summary = s.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == account.id,
            UsageSummary.period == "2026-06",
        )
    )
    assert summary is not None
    s.close()


def test_build_payload_missing_primary(status_env):
    sf = status_env["sf"]
    owner = status_env["owner"]
    team_id = status_env["team_id"]

    s = sf()
    tool_repo = ToolCenterRepository(s, team_id)
    orphan = tool_repo.list_accounts()[2]
    tool_repo.update_account(orphan.id, primary_member_id=None, status="trial")
    s.commit()
    payload = build_submission_status_payload(s, team_id, "2026-06", owner)
    row = next(r for r in payload["accounts"] if r["account_id"] == orphan.id)
    assert row["submission_state"] == "missing_primary"
    s.close()
