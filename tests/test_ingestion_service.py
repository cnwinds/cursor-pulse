from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from pulse.ingestion.adapters.cursor_api import CursorApiAdapter
from pulse.ingestion.daily import rebuild_daily_aggregates
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
        channel_user_id="dt-ingest-1",
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
    assert daily.kind_family == "included"
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
        channel_user_id="dt-ingest-2",
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


def test_daily_agg_splits_included_and_byok_same_model(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(team_id=team.id, display_name="T", channel_user_id="dt-kind", status="active")
    session.add(member)
    session.flush()
    ingestion = UsageIngestion(
        member_id=member.id,
        account_id=account.id,
        vendor_id=account.vendor_id,
        billing_period="2026-07",
        source_type="api_sync",
        channel="test",
        status="confirmed",
        triggered_by=member.id,
        event_count=2,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(ingestion)
    session.flush()
    day = date(2026, 7, 8)
    session.add_all(
        [
            UsageRecord(
                ingestion_id=ingestion.id,
                member_id=member.id,
                event_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
                event_date=day,
                kind="USAGE_EVENT_KIND_INCLUDED_IN_PRO",
                model="GLM-5.2",
                tokens_input_no_cache=100,
                tokens_output=10,
                tokens_total=110,
                cost_raw="included",
                cost_usd=0,
                source_row_hash="h-inc",
            ),
            UsageRecord(
                ingestion_id=ingestion.id,
                member_id=member.id,
                event_at=datetime(2026, 7, 8, 1, tzinfo=timezone.utc),
                event_date=day,
                kind="USAGE_EVENT_KIND_USER_API_KEY",
                model="GLM-5.2",
                tokens_input_no_cache=200,
                tokens_output=20,
                tokens_total=220,
                cost_raw="none",
                cost_usd=0,
                source_row_hash="h-byok",
            ),
        ]
    )
    session.flush()
    rebuild_daily_aggregates(session, account.id, {day})
    session.flush()
    rows = session.scalars(
        select(UsageDailyAggregate).where(UsageDailyAggregate.account_id == account.id)
    ).all()
    by_family = {row.kind_family: row for row in rows}
    assert set(by_family) == {"included", "user_api_key"}
    assert by_family["included"].tokens_input == 100
    assert by_family["user_api_key"].tokens_input == 200


def test_daily_agg_uses_effective_pool_cost_for_estimated_included(session):
    """Included events often have cost_usd=0; board summary uses cost_estimated_usd."""
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(team_id=team.id, display_name="T", channel_user_id="dt-cost", status="active")
    session.add(member)
    session.flush()
    ingestion = UsageIngestion(
        member_id=member.id,
        account_id=account.id,
        vendor_id=account.vendor_id,
        billing_period="2026-07",
        source_type="api_sync",
        channel="test",
        status="confirmed",
        triggered_by=member.id,
        event_count=1,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(ingestion)
    session.flush()
    day = date(2026, 7, 17)
    session.add(
        UsageRecord(
            ingestion_id=ingestion.id,
            member_id=member.id,
            event_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
            event_date=day,
            kind="USAGE_EVENT_KIND_INCLUDED_IN_PRO",
            model="claude-opus-4-8-thinking-high",
            tokens_input_no_cache=1000,
            tokens_output=100,
            tokens_total=1100,
            cost_raw="included",
            cost_usd=0,
            cost_estimated_usd=23.5376,
            cost_basis="estimated",
            source_row_hash="h-est",
        )
    )
    session.flush()
    rebuild_daily_aggregates(session, account.id, {day})
    session.flush()
    row = session.scalars(
        select(UsageDailyAggregate).where(UsageDailyAggregate.account_id == account.id)
    ).one()
    assert float(row.total_cost_usd) == pytest.approx(23.5376)


def test_backfill_unknown_daily_kind_family_from_records(session):
    from pulse.ingestion.daily import backfill_unknown_daily_kind_families

    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    account = next(a for a in tool_repo.list_active_accounts() if a.vendor.slug == "cursor")
    member = Member(team_id=team.id, display_name="T", channel_user_id="dt-bf", status="active")
    session.add(member)
    session.flush()
    ingestion = UsageIngestion(
        member_id=member.id,
        account_id=account.id,
        vendor_id=account.vendor_id,
        billing_period="2026-07",
        source_type="api_sync",
        channel="test",
        status="confirmed",
        triggered_by=member.id,
        event_count=1,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(ingestion)
    session.flush()
    day = date(2026, 7, 9)
    session.add(
        UsageRecord(
            ingestion_id=ingestion.id,
            member_id=member.id,
            event_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
            event_date=day,
            kind="USAGE_EVENT_KIND_USER_API_KEY",
            model="GLM-5.2",
            tokens_input_no_cache=50,
            tokens_output=5,
            tokens_total=55,
            cost_raw="none",
            cost_usd=0,
            source_row_hash="h-bf",
        )
    )
    session.add(
        UsageDailyAggregate(
            account_id=account.id,
            event_date=day,
            model="GLM-5.2",
            kind_family="unknown",
            event_count=1,
            tokens_input=50,
            tokens_output=5,
            tokens_cache_read=0,
        )
    )
    session.flush()
    updated = backfill_unknown_daily_kind_families(session)
    session.flush()
    assert updated == 1
    rows = session.scalars(
        select(UsageDailyAggregate).where(UsageDailyAggregate.account_id == account.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].kind_family == "user_api_key"
