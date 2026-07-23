from pathlib import Path

from pulse.aggregate.engine import _records_for_period, aggregate_period
from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.storage.db import init_db
from pulse.storage.models import UsageIngestion
from tests.conftest import make_team_repo

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"


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
    assert m1["total_events"] == 4
    assert m1["member_count_reported"] == 1

    session.close()


def test_aggregate_includes_account_sync_ingestions_without_member_id(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'account-sync.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    team, repo = make_team_repo(session)

    member = repo.add_member("user-1", "Alice")
    parsed = parse_usage_events_csv(SAMPLE)
    ingestion = repo.save_csv_ingestion(
        member=member,
        period="2026-07",
        parsed=parsed,
        submit_channel="private",
    )
    ingestion.member_id = None
    ingestion.account_id = "acct-1"
    session.flush()

    from datetime import date

    from pulse.storage.models import AiAccount, AiPlan, AiVendor

    vendor = AiVendor(slug="cursor", name="Cursor")
    session.add(vendor)
    session.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        slug="pro",
        plan_name="Pro",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
        effective_from=date(2026, 1, 1),
    )
    session.add(plan)
    session.flush()
    account = AiAccount(
        id="acct-1",
        team_id=team.id,
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="alice@example.com",
        primary_member_id=member.id,
    )
    session.add(account)
    session.commit()

    records = _records_for_period(session, "2026-07", team_id=team.id)
    assert len(records) == 4

    metrics = aggregate_period(session, "2026-07", team_id=team.id)
    assert metrics["total_events"] == 4
    assert metrics["member_count_reported"] == 1

    session.close()


def test_aggregate_includes_ledger_participation_counts(tmp_path):
    from datetime import date

    from pulse.storage.models import AiAccount, AiPlan, AiVendor

    db_url = f"sqlite:///{tmp_path / 'ledger.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    team, repo = make_team_repo(session)
    member = repo.add_member("user-1", "Alice")
    parsed = parse_usage_events_csv(SAMPLE)
    ingestion = repo.save_csv_ingestion(
        member=member,
        period="2026-07",
        parsed=parsed,
        submit_channel="private",
    )
    ingestion.member_id = None
    ingestion.account_id = "acct-ledger-1"
    session.flush()

    vendor = AiVendor(slug="cursor", name="Cursor")
    session.add(vendor)
    session.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        slug="pro",
        plan_name="Pro",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
        effective_from=date(2026, 1, 1),
    )
    session.add(plan)
    session.flush()
    session.add_all(
        [
            AiAccount(
                id="acct-ledger-1",
                team_id=team.id,
                vendor_id=vendor.id,
                plan_id=plan.id,
                account_identifier="alice@example.com",
                primary_member_id=member.id,
            ),
            AiAccount(
                id="acct-ledger-2",
                team_id=team.id,
                vendor_id=vendor.id,
                plan_id=plan.id,
                account_identifier="bob@example.com",
                primary_member_id=member.id,
                status="shared",
            ),
        ]
    )
    repo.commit()

    metrics = aggregate_period(session, "2026-07", team_id=team.id)
    assert metrics["account_count_ledger"] == 2
    assert metrics["account_count_submitted"] == 1
    assert metrics["account_count_unsubmitted"] == 1

    session.close()
