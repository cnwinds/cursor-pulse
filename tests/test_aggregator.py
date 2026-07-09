from pathlib import Path

from pulse.aggregate.engine import aggregate_period
from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.storage.db import init_db
from tests.conftest import make_team_repo

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"


def test_aggregate_reproducible(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    team, repo = make_team_repo(session)

    member = repo.add_member("user-1", "Alice")
    parsed = parse_usage_events_csv(SAMPLE)
    repo.save_csv_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
    )
    repo.commit()

    m1 = aggregate_period(session, "2026-06", team_id=team.id)
    session.commit()
    m2 = aggregate_period(session, "2026-06", team_id=team.id)
    session.commit()

    keys = ["total_events", "total_tokens", "total_cost_usd"]
    for key in keys:
        assert m1[key] == m2[key]
    assert m1["total_events"] == 498
    assert m1["member_count_reported"] == 1

    session.close()
