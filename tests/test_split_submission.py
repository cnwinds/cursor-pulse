from pathlib import Path

import pytest
from sqlalchemy import func, select

from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.extract.period_split import split_parsed_by_period
from pulse.storage.db import init_db
from pulse.storage.models import UsageIngestion, UsageRecord
from tests.conftest import make_team_repo

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"


@pytest.fixture
def repo_session(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'split.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    _team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    yield session, repo, member
    session.close()


def test_save_split_ingestions_creates_one_ingestion_per_month(repo_session):
    session, repo, member = repo_session
    parsed = parse_usage_events_csv(SAMPLE)

    results = repo.save_split_ingestions(
        member=member,
        parsed=parsed,
        submit_channel="private",
        input_type="csv",
    )
    repo.commit()

    assert [p for p, _ in results] == ["2026-03", "2026-04", "2026-05", "2026-06"]
    ingestions = session.scalars(select(UsageIngestion).order_by(UsageIngestion.billing_period)).all()
    assert len(ingestions) == 4
    assert [s.billing_period for s in ingestions] == ["2026-03", "2026-04", "2026-05", "2026-06"]
    assert all(s.status == "confirmed" for s in ingestions)

    total_records = session.scalar(select(func.count()).select_from(UsageRecord))
    assert total_records == parsed.summary.event_count


def test_save_split_ingestions_overwrites_existing_period(repo_session):
    session, repo, member = repo_session
    parsed = parse_usage_events_csv(SAMPLE)
    splits = split_parsed_by_period(parsed)

    repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=splits["2026-06"],
        submit_channel="private",
    )
    repo.commit()
    first_id = session.scalar(
        select(UsageIngestion.id).where(UsageIngestion.billing_period == "2026-06")
    )

    repo.save_split_ingestions(
        member=member,
        parsed=parsed,
        submit_channel="private",
        input_type="csv",
    )
    repo.commit()

    june_ingestions = session.scalars(
        select(UsageIngestion).where(UsageIngestion.billing_period == "2026-06")
    ).all()
    assert len(june_ingestions) == 1
    assert june_ingestions[0].id != first_id


def test_save_split_ingestions_removes_stale_default_period(repo_session):
    session, repo, member = repo_session
    parsed = parse_usage_events_csv(SAMPLE)

    repo.save_ingestion(
        member=member,
        period="2026-07",
        parsed=parsed,
        submit_channel="private",
    )
    repo.commit()
    assert session.scalar(select(func.count()).select_from(UsageIngestion)) == 1

    repo.save_split_ingestions(
        member=member,
        parsed=parsed,
        submit_channel="private",
        default_period="2026-07",
        input_type="csv",
    )
    repo.commit()

    periods = session.scalars(select(UsageIngestion.billing_period).order_by(UsageIngestion.billing_period)).all()
    assert periods == ["2026-03", "2026-04", "2026-05", "2026-06"]
    assert "2026-07" not in periods
