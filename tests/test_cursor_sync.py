from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync import CursorSyncService
from pulse.integrations.cursor_api import map_usage_event
from pulse.storage.db import init_db
from pulse.storage.models import AiAccountCredential, Member, UsageIngestion, UsageRecord, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_successful_sync_writes_records(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(
        a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor"
    )
    cursor_account.primary_member_id = None
    session.flush()

    member = Member(
        team_id=team.id,
        display_name="Sync Tester",
        dingtalk_user_id="dt-sync-1",
        status="active",
    )
    session.add(member)
    session.flush()
    cursor_account.primary_member_id = member.id
    session.commit()

    mock_client = MagicMock()
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=cursor_account.account_identifier.lower())
    mock_client.exchange_api_key.return_value = "session-token"
    mock_client.get_current_period_usage.return_value = json.loads(
        (FIXTURES / "cursor_period_usage.json").read_text()
    )
    raw_event = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    dto = map_usage_event(raw_event)
    mock_client.iter_filtered_usage_events.return_value = iter([dto])

    cred_service = CredentialService(session, TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=cursor_account.id,
        api_key="crsr_test_api_key_abcdefghijklmnop",
        member_id=member.id,
    )

    sync_service = CursorSyncService(session, TEST_KEY, cursor_client=mock_client)
    result = sync_service.sync_account(cursor_account.id, channel="scheduler")

    assert result.status == "confirmed"
    assert result.event_count == 1

    record_count = session.scalar(select(func.count()).select_from(UsageRecord))
    assert record_count == 1

    ingestion = session.get(UsageIngestion, result.ingestion_id)
    assert ingestion is not None
    assert ingestion.channel == "scheduler"
    assert ingestion.source_type == "api_sync"
    assert ingestion.status == "confirmed"
    assert ingestion.metadata_json["sync_source"] == "cursor_api"
    assert "period_usage" in ingestion.metadata_json

    summary = session.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == cursor_account.id,
            UsageSummary.period == dto.event_date.strftime("%Y-%m"),
        )
    )
    assert summary is not None
    assert summary.sync_source == "api"

    cred = session.scalar(
        select(AiAccountCredential).where(
            AiAccountCredential.account_id == cursor_account.id
        )
    )
    assert cred.last_sync_status == "success"
    assert cred.last_sync_at is not None
    assert cred.last_sync_error is None
