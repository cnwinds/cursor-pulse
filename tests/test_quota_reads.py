from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pulse.storage.db import init_db
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, AiVendor
from pulse.tool_center.quota_reads import (
    latest_snapshots_for_accounts,
    latest_snapshots_for_team,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo


@pytest.fixture
def qr_env():
    sf = init_db("sqlite:///:memory:")
    session = sf()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    tool_repo = ToolCenterRepository(session, team.id)
    cursor_accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    assert cursor_accounts
    cursor_acc = cursor_accounts[0]

    other_vendor = AiVendor(slug="other-qr", name="Other", is_active=True)
    session.add(other_vendor)
    session.flush()
    other_acc = AiAccount(
        vendor_id=other_vendor.id,
        plan_id=cursor_acc.plan_id,
        team_id=team.id,
        account_identifier="other-qr@example.com",
        status="shared",
    )
    inactive = AiAccount(
        vendor_id=cursor_acc.vendor_id,
        plan_id=cursor_acc.plan_id,
        team_id=team.id,
        account_identifier="retired-qr@example.com",
        status="retired",
    )
    session.add_all([other_acc, inactive])
    session.flush()

    today = date.today()
    now = datetime.now(timezone.utc)
    older = now - timedelta(hours=2)
    session.add_all(
        [
            AccountQuotaSnapshot(
                account_id=cursor_acc.id,
                captured_at=older,
                cycle_start=today - timedelta(days=5),
                cycle_end=today + timedelta(days=25),
                limit_cents=7000,
                used_cents=1000,
                remaining_cents=6000,
                total_pct=10.0,
            ),
            AccountQuotaSnapshot(
                account_id=cursor_acc.id,
                captured_at=now,
                cycle_start=today - timedelta(days=5),
                cycle_end=today + timedelta(days=25),
                limit_cents=7000,
                used_cents=2000,
                remaining_cents=5000,
                total_pct=20.0,
            ),
            AccountQuotaSnapshot(
                account_id=other_acc.id,
                captured_at=now,
                cycle_start=today - timedelta(days=5),
                cycle_end=today + timedelta(days=25),
                limit_cents=1000,
                used_cents=100,
                remaining_cents=900,
                total_pct=10.0,
            ),
            AccountQuotaSnapshot(
                account_id=inactive.id,
                captured_at=now,
                cycle_start=today - timedelta(days=5),
                cycle_end=today + timedelta(days=25),
                limit_cents=7000,
                used_cents=7000,
                remaining_cents=0,
                total_pct=100.0,
            ),
        ]
    )
    session.commit()
    yield {
        "session": session,
        "team_id": team.id,
        "cursor_id": cursor_acc.id,
        "other_id": other_acc.id,
        "inactive_id": inactive.id,
    }
    session.close()


def test_latest_snapshots_for_accounts_picks_newest(qr_env):
    session = qr_env["session"]
    latest = latest_snapshots_for_accounts(session, [qr_env["cursor_id"]])
    assert set(latest) == {qr_env["cursor_id"]}
    assert latest[qr_env["cursor_id"]].total_pct == 20.0


def test_latest_snapshots_for_team_defaults_to_active_cursor(qr_env):
    latest = latest_snapshots_for_team(qr_env["session"], qr_env["team_id"])
    assert qr_env["cursor_id"] in latest
    assert qr_env["other_id"] not in latest
    assert qr_env["inactive_id"] not in latest
    assert latest[qr_env["cursor_id"]].total_pct == 20.0


def test_latest_snapshots_for_team_all_vendors_and_inactive(qr_env):
    latest = latest_snapshots_for_team(
        qr_env["session"],
        qr_env["team_id"],
        vendor_slug=None,
        active_only=False,
    )
    assert qr_env["cursor_id"] in latest
    assert qr_env["other_id"] in latest
    assert qr_env["inactive_id"] in latest
