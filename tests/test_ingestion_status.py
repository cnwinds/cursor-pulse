from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import AiAccountMember, Base, Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import ingest_cursor_fixture, make_team_repo


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
    owner = bootstrap_portal_owner(repo, channel_user_id="admin", display_name="Admin", password="x")
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


def test_ingestion_status_admin_sees_all_accounts(status_env):
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


def test_ingestion_status_cursor_accounts_show_no_credential(status_env):
    client = status_env["client"]
    config = status_env["config"]
    owner = status_env["owner"]
    token = create_access_token(config, owner)
    res = client.get(
        "/api/v2/ingestion-status",
        params={"period": "2026-06"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    cursor_rows = [
        r
        for r in res.json()["accounts"]
        if r.get("vendor_slug") == "cursor" and r.get("primary_member_id")
    ]
    assert len(cursor_rows) == 2
    assert all(r["ingestion_state"] == "no_credential" for r in cursor_rows)


def test_ingestion_status_member_sees_primary_and_shared(status_env):
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


def test_api_sync_creates_usage_summary(status_env):
    sf = status_env["sf"]
    member_id = status_env["member"].id
    account_id = status_env["account"].id
    team_id = status_env["team_id"]

    s = sf()
    tool_repo = ToolCenterRepository(s, team_id)
    account = tool_repo.get_account(account_id)
    ingest_cursor_fixture(
        s,
        team_id=team_id,
        account_id=account_id,
        vendor_id=account.vendor_id,
        member_id=member_id,
        period="2026-06",
    )
    s.commit()
    s.close()

    s = sf()
    from pulse.storage.models import UsageSummary

    summary = s.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == account_id,
            UsageSummary.period == "2026-06",
        )
    )
    assert summary is not None
    assert summary.sync_source == "api"
    s.close()


def test_build_payload_missing_primary(status_env):
    from pulse.tool_center.ingestion_status import build_ingestion_status_payload

    sf = status_env["sf"]
    owner = status_env["owner"]
    team_id = status_env["team_id"]

    s = sf()
    tool_repo = ToolCenterRepository(s, team_id)
    orphan = tool_repo.list_accounts()[2]
    tool_repo.update_account(orphan.id, primary_member_id=None, status="trial")
    s.commit()
    payload = build_ingestion_status_payload(s, team_id, "2026-06", owner)
    row = next(r for r in payload["accounts"] if r["account_id"] == orphan.id)
    assert row["ingestion_state"] == "missing_primary"
    s.close()
