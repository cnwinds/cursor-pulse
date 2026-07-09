from pathlib import Path

import pytest

from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.query.engine import answer_question, looks_like_query
from pulse.storage.db import init_db
from tests.conftest import make_team_repo

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"


def test_looks_like_query():
    assert looks_like_query("谁用得最多")
    assert not looks_like_query("hello")


def test_query_ranking_admin(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'q.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    _team, repo = make_team_repo(session)
    repo.add_member("admin1", "Admin")
    member = repo.add_member("u1", "Alice")
    parsed = parse_usage_events_csv(SAMPLE)
    repo.save_csv_ingestion(
        member=member, period="2026-06", parsed=parsed, submit_channel="private"
    )
    repo.commit()

    result = answer_question(
        session,
        "谁用得最多",
        user_id="admin1",
        admin_user_ids=["admin1"],
        period="2026-06",
    )
    assert "Alice" in result.answer
    assert "排名" in result.answer
    session.close()


def test_query_self_only_non_admin(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'q2.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    _team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    parsed = parse_usage_events_csv(SAMPLE)
    repo.save_csv_ingestion(
        member=member, period="2026-06", parsed=parsed, submit_channel="private"
    )
    repo.commit()

    result = answer_question(
        session,
        "谁用得最多",
        user_id="u1",
        admin_user_ids=["admin1"],
        period="2026-06",
    )
    assert "你的" in result.answer
    assert "498" in result.answer or "请求" in result.answer
    session.close()
