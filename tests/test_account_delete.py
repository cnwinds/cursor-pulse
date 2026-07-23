from __future__ import annotations

import pytest
from sqlalchemy import select

from pulse.storage.db import init_db
from pulse.storage.models import AiAccount, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


@pytest.fixture
def account_env(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.commit()
    tool_repo = ToolCenterRepository(session, team.id)
    zhipu_vendor = next(v for v in tool_repo.list_vendors() if v.slug == "zhipu")
    plan = next(p for p in tool_repo.list_plans(zhipu_vendor.id))
    account = tool_repo.create_account(
        vendor_id=zhipu_vendor.id,
        plan_id=plan.id,
        account_identifier="delete-me@test.com",
        status="trial",
    )
    session.commit()
    return tool_repo, account


def test_hard_delete_when_no_associations(account_env, session):
    repo, account = account_env
    account_id = account.id

    mode = repo.delete_account(account_id)
    session.commit()

    assert mode == "hard"
    assert session.get(AiAccount, account_id) is None
    assert repo.get_account(account_id) is None
    assert account_id not in {a.id for a in repo.list_accounts()}


def test_soft_delete_when_usage_summary_exists(account_env, session):
    repo, account = account_env
    account_id = account.id

    repo.upsert_usage_summary(
        account_id=account_id,
        period="2026-07",
        ingestion_id="ing-1",
        submitted_by_member_id="",
        summary={
            "primary_metric_value": 10.0,
            "primary_metric_unit": "usd",
            "quota_usage_ratio": 50.0,
            "breakdown_by_model": {},
        },
    )
    session.commit()

    mode = repo.delete_account(account_id)
    session.commit()

    assert mode == "soft"
    row = session.get(AiAccount, account_id)
    assert row is not None
    assert row.deleted_at is not None
    assert repo.get_account(account_id) is None
    assert account_id not in {a.id for a in repo.list_accounts()}
    summary = session.scalar(
        select(UsageSummary).where(UsageSummary.account_id == account_id)
    )
    assert summary is not None
