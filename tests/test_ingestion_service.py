from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from pulse.ingestion.adapters.cursor_api import CursorApiAdapter
from pulse.ingestion.service import UsageIngestionService
from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import map_usage_event
from pulse.storage.db import init_db
from pulse.storage.models import Member, UsageDailyAggregate, UsageIngestion, UsageRecord, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_ingest_writes_records_summary_and_daily_agg(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(
        a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor"
    )

    member = Member(
        team_id=team.id,
        display_name="Tester",
        dingtalk_user_id="dt-ingest-1",
        status="active",
    )
    session.add(member)
    session.flush()

    raw = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    dto = map_usage_event(raw)

    context = IngestionContext(
        account_id=cursor_account.id,
        vendor_id=cursor_account.vendor_id,
        vendor_slug="cursor",
        billing_period="2026-07",
        member_id=member.id,
        channel="test",
        source_type="api_sync",
        triggered_by=member.id,
        events=[dto],
        metadata={"source": "test"},
    )

    service = UsageIngestionService(session, team.id)
    result = service.ingest(context=context, adapter=CursorApiAdapter())

    assert result.status == "confirmed"
    assert result.event_count == 1

    ingestion = session.get(UsageIngestion, result.ingestion_id)
    assert ingestion is not None
    assert ingestion.account_id == cursor_account.id
    assert ingestion.event_count == 1

    record_count = session.scalar(select(func.count()).select_from(UsageRecord))
    assert record_count == 1

    record = session.scalar(select(UsageRecord))
    assert record.model == "composer-2.5"
    assert record.ingestion_id == result.ingestion_id
    assert record.external_id == dto.external_id

    summary = session.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == cursor_account.id,
            UsageSummary.period == "2026-07",
        )
    )
    assert summary is not None
    assert summary.latest_ingestion_id == result.ingestion_id
    assert summary.sync_source == "api"
    assert summary.last_synced_at is not None

    daily = session.scalar(select(UsageDailyAggregate))
    assert daily is not None
    assert daily.account_id == cursor_account.id
    assert daily.model == "composer-2.5"
    assert daily.event_count == 1


def test_confirmed_ingest_replaces_old_period_records(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_account = next(
        a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor"
    )

    member = Member(
        team_id=team.id,
        display_name="Tester",
        dingtalk_user_id="dt-ingest-2",
        status="active",
    )
    session.add(member)
    session.flush()

    raw = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    dto = map_usage_event(raw)

    service = UsageIngestionService(session, team.id)
    adapter = CursorApiAdapter()

    context = IngestionContext(
        account_id=cursor_account.id,
        vendor_id=cursor_account.vendor_id,
        vendor_slug="cursor",
        billing_period="2026-07",
        member_id=member.id,
        channel="test",
        source_type="api_sync",
        triggered_by=member.id,
        events=[dto],
    )
    first = service.ingest(context=context, adapter=adapter)

    second = service.ingest(context=context, adapter=adapter)

    ingestion_count = session.scalar(select(func.count()).select_from(UsageIngestion))
    assert ingestion_count == 1

    record_count = session.scalar(select(func.count()).select_from(UsageRecord))
    assert record_count == 1

    summary = session.scalar(select(UsageSummary))
    assert summary.latest_ingestion_id == second.ingestion_id
    assert summary.latest_ingestion_id != first.ingestion_id
