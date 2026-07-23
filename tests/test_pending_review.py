from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_pending_review_confirm_flow():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)

    from pulse.domain import ParsedCsv, ParseSummary
    from datetime import date
    from decimal import Decimal

    member = repo.add_member("u1", "Alice")
    summary = ParseSummary(
        period_hint="2026-06",
        date_min=date(2026, 6, 1),
        date_max=date(2026, 6, 2),
        event_count=0,
        total_tokens=0,
        total_cost_usd=Decimal("0"),
        top_models=[],
        all_included_or_free=True,
    )
    parsed = ParsedCsv(records=[], summary=summary)
    pending = repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
        input_type="screenshot",
        status="pending_review",
        extraction_confidence=0.6,
    )
    assert pending.status == "pending_review"
    assert repo.get_submitted_member_ids("2026-06") == set()

    repo.confirm_ingestion(pending.id)
    assert repo.get_submitted_member_ids("2026-06") == {member.id}
    session.close()


def test_reject_pending_ingestion():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    _team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Bob")

    from pulse.domain import ParsedCsv, ParseSummary
    from datetime import date
    from decimal import Decimal

    summary = ParseSummary(
        period_hint="2026-06",
        date_min=date(2026, 6, 1),
        date_max=date(2026, 6, 2),
        event_count=0,
        total_tokens=0,
        total_cost_usd=Decimal("0"),
        top_models=[],
        all_included_or_free=True,
    )
    parsed = ParsedCsv(records=[], summary=summary)
    pending = repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
        status="pending_review",
    )
    repo.reject_ingestion(pending.id)
    assert repo.list_pending_ingestions("2026-06") == []
    session.close()
