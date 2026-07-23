from __future__ import annotations

def _msg(result):
    data = result.result or {}
    return result.user_message or data.get("text") or data.get("answer") or ""


from datetime import date, datetime, timezone

import pytest

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.quota_self_read import (
    _format_user_message,
    handle_quota_self_read,
)
from pulse.capabilities.invoke import HANDLERS, invoke_capability
from pulse.config import AppConfig, CredentialConfig
from pulse.storage.db import init_db
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


@pytest.fixture
def quota_env(session):
    team, repo = make_team_repo(session)
    actor = repo.add_member("actor-user", "Actor")
    other = repo.add_member("other-user", "Other")
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_accounts = [
        a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"
    ]
    actor_account = cursor_accounts[0]
    other_account = cursor_accounts[1]
    tool_repo.update_account(
        actor_account.id, primary_member_id=actor.id, status="shared"
    )
    tool_repo.update_account(
        other_account.id, primary_member_id=other.id, status="shared"
    )

    snap = AccountQuotaSnapshot(
        account_id=actor_account.id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        limit_cents=7000,
        used_cents=2000,
        remaining_cents=5000,
        total_pct=28.5,
        auto_pct=27.0,
        api_pct=10.0,
    )
    session.add(snap)
    repo.commit()

    config = AppConfig(credentials=CredentialConfig(encryption_key=""))
    return {
        "team": team,
        "actor": actor,
        "other": other,
        "actor_account": actor_account,
        "other_account": other_account,
        "config": config,
    }


def _request(*, team_id: str, actor_member_id: str) -> CapabilityInvokeRequest:
    return CapabilityInvokeRequest(
        invocation_id="inv-1",
        idempotency_key="idem-1",
        team_id=team_id,
        actor_member_id=actor_member_id,
        capability_key="quota.self.read",
        capability_version="1",
    )


def test_handler_registered():
    assert ("quota.self.read", "1") in HANDLERS


def test_quota_self_read_returns_actor_accounts_only(session, quota_env):
    request = _request(
        team_id=quota_env["team"].id,
        actor_member_id=quota_env["actor"].id,
    )
    result = handle_quota_self_read(
        session, request=request, config=quota_env["config"], op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    accounts = result.result["accounts"]
    assert accounts
    item = accounts[0]
    assert item.get("total_pct") == 28.5 or item.get("auto_pct") == 27.0
    assert item.get("api_pct") == 10.0

    account_ids = {item["account_id"] for item in result.result["accounts"]}
    assert quota_env["actor_account"].id in account_ids
    assert quota_env["other_account"].id not in account_ids


def test_quota_self_read_no_primary_cursor_account(session, quota_env):
    request = _request(
        team_id=quota_env["team"].id,
        actor_member_id=quota_env["other"].id,
    )
    tool_repo = ToolCenterRepository(session, quota_env["team"].id)
    tool_repo.update_account(quota_env["other_account"].id, primary_member_id=None)
    session.flush()

    result = handle_quota_self_read(
        session, request=request, config=quota_env["config"], op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    assert result.result.get("empty_reason") == "no_cursor_account"
    assert result.result["accounts"] == []


def test_invoke_capability_quota_self_read(session, quota_env):
    request = _request(
        team_id=quota_env["team"].id,
        actor_member_id=quota_env["actor"].id,
    )
    result = invoke_capability(
        session, request=request, config=quota_env["config"]
    )

    assert result.status == "succeeded"
    assert len(result.result["accounts"]) == 1


def test_quota_self_read_rejects_wrong_team(session, quota_env):
    request = _request(
        team_id="wrong-team-id",
        actor_member_id=quota_env["actor"].id,
    )
    result = handle_quota_self_read(
        session, request=request, config=quota_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "forbidden"


def test_format_user_message_includes_cursor_sub_progress_table():
    message = _format_user_message(
        [
            {
                "account_identifier": "a@example.com",
                "has_snapshot": True,
                "status": "healthy",
                "total_pct": 31.8,
                "auto_pct": 27.0,
                "api_pct": 10.0,
                "remaining_headroom_pct": 68.2,
                "quota_progress": 0.318,
                "api_limit_usd": 70.0,
                "captured_at": "2026-07-15T04:30:00+00:00",
            },
            {
                "account_identifier": "b@example.com",
                "has_snapshot": True,
                "status": "healthy",
                "total_pct": 36.4,
                "auto_pct": 30.0,
                "api_pct": 12.5,
                "remaining_headroom_pct": 63.6,
                "quota_progress": 0.364,
                "api_limit_usd": None,
            },
        ]
    )
    assert "a@example.com" in message
    assert "b@example.com" in message
    assert message.count("| Auto + Composer |") == 2
    assert "| Total | 31.8% |" in message
    assert "| API | 12.5% |" in message
    assert "套餐含至少 $70 API 用量" in message
    assert "数据最后更新：2026-07-15 12:30:00" in message
