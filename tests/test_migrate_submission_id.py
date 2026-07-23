from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync import CursorSyncService
from pulse.integrations.cursor_api import map_usage_event
from pulse.storage.db import init_db
from pulse.storage.migrate import migrate_schema
from pulse.storage.models import Base, Member, UsageRecord
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def _legacy_usage_records_sql() -> str:
    return """
    CREATE TABLE usage_records (
        id VARCHAR(36) PRIMARY KEY,
        submission_id VARCHAR(36) NOT NULL,
        member_id VARCHAR(36) NOT NULL,
        event_at DATETIME NOT NULL,
        event_date DATE NOT NULL,
        kind VARCHAR(64) NOT NULL,
        model VARCHAR(128) NOT NULL,
        max_mode BOOLEAN NOT NULL,
        tokens_input_cache_write INTEGER NOT NULL,
        tokens_input_no_cache INTEGER NOT NULL,
        tokens_cache_read INTEGER NOT NULL,
        tokens_output INTEGER NOT NULL,
        tokens_total INTEGER NOT NULL,
        cost_raw VARCHAR(16) NOT NULL,
        cost_usd NUMERIC(12, 4) NOT NULL,
        cloud_agent_id VARCHAR(128),
        automation_id VARCHAR(128),
        source_row_hash VARCHAR(64) NOT NULL,
        extraction_confidence FLOAT NOT NULL,
        created_at DATETIME NOT NULL
    )
    """


@pytest.fixture
def legacy_engine(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS usage_records"))
        conn.execute(text(_legacy_usage_records_sql()))
        conn.execute(text("ALTER TABLE usage_records ADD COLUMN ingestion_id VARCHAR(36)"))
    migrate_schema(engine)
    return engine


def test_migrate_drops_legacy_submission_id_column(legacy_engine):
    columns = {col["name"] for col in inspect(legacy_engine).get_columns("usage_records")}
    assert "submission_id" not in columns
    assert "ingestion_id" in columns


def test_api_sync_inserts_after_legacy_migration(legacy_engine):
    session_factory = sessionmaker(bind=legacy_engine, autoflush=False, autocommit=False)
    session = session_factory()
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(
        a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor"
    )
    member = Member(
        team_id=team.id,
        display_name="Sync Tester",
        dingtalk_user_id="dt-legacy-1",
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
    result = sync_service.sync_account(cursor_account.id, channel="web")

    assert result.event_count == 1
    record = session.query(UsageRecord).one()
    assert record.ingestion_id == result.ingestion_id
    session.close()
