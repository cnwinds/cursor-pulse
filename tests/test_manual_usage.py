from __future__ import annotations

import pytest
from sqlalchemy import select

from pulse.storage.db import init_db
from pulse.storage.models import AiAccount, Member, UsageSummary
from pulse.tool_center.manual import (
    ManualUsageService,
    infer_vendor_slug_from_text,
    looks_like_manual_usage,
    parse_manual_usage_text,
    pick_account_for_screenshot,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.tool_center.usage import build_manual_usage_summary, infer_metric_unit_for_plan
from pulse.extract.vendor_vision import parse_vendor_vision_response
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def _member(session, team_id, name="Alice"):
    m = Member(
        team_id=team_id,
        dingtalk_user_id=f"u-{name}",
        display_name=name,
        status="active",
    )
    session.add(m)
    session.flush()
    return m


def test_parse_manual_usage_text():
    assert looks_like_manual_usage("上报 智谱 85")
    assert infer_vendor_slug_from_text("提交智谱的用量") == "zhipu"
    cmd = parse_manual_usage_text("用量 minimax 12000 calls")
    assert cmd.vendor_slug == "minimax"
    assert cmd.metric_value == 12000
    assert cmd.metric_unit == "calls"


def test_infer_metric_unit_for_zhipu_plan(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    plan = next(p for p in tool_repo.list_plans() if p.slug == "glm_coding_lite")
    assert infer_metric_unit_for_plan(plan) == "calls"


def test_build_manual_usage_summary_optional_ratio(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    plan = next(p for p in tool_repo.list_plans() if p.slug == "glm_coding_lite")
    summary = build_manual_usage_summary(plan=plan, metric_value=85, metric_unit="calls")
    assert summary["primary_metric_unit"] == "calls"
    assert summary["quota_usage_ratio"] == 85.0


def test_manual_submission_creates_usage_summary(session):
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    vendor = tool_repo.get_vendor_by_slug("zhipu")
    plan = next(p for p in tool_repo.list_plans(vendor.id) if p.slug == "glm_coding_lite")
    member = _member(session, team.id)
    account = tool_repo.create_account(
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="glm@company.com",
        status="dedicated",
        primary_member_id=member.id,
    )
    session.flush()

    cmd = parse_manual_usage_text("上报 智谱 85")
    svc = ManualUsageService(session, team.id)
    submission, saved_account, summary = svc.submit_for_member(
        member=member,
        period="2026-06",
        command=cmd,
        submit_channel="private",
        repo=repo,
    )
    repo.commit()

    assert submission.source_type == "manual_text"
    assert saved_account.id == account.id
    assert summary["primary_metric_value"] == 85

    row = session.scalar(
        select(UsageSummary).where(
            UsageSummary.account_id == account.id,
            UsageSummary.period == "2026-06",
        )
    )
    assert row is not None
    assert float(row.primary_metric_value) == 85
    assert row.primary_metric_unit == "calls"


def test_pick_account_for_screenshot_prefers_single_non_cursor(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    cursor = tool_repo.list_accounts()[0]
    vendor = tool_repo.get_vendor_by_slug("zhipu")
    plan = tool_repo.list_plans(vendor.id)[0]
    zhipu = tool_repo.create_account(
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="z@co.com",
        status="dedicated",
    )
    session.flush()
    cursor = tool_repo.get_account(cursor.id)
    zhipu = tool_repo.get_account(zhipu.id)
    picked = pick_account_for_screenshot([cursor, zhipu])
    assert picked is not None
    assert picked.vendor.slug == "zhipu"


def test_parse_vendor_vision_response():
    raw = """
    {
      "confidence": 0.92,
      "warnings": [],
      "period_hint": "2026-06",
      "primary_metric_value": 45,
      "primary_metric_unit": "messages",
      "breakdown_by_model": {"GPT": 45}
    }
    """
    result = parse_vendor_vision_response(raw)
    assert result.primary_metric_value == 45
    assert result.primary_metric_unit == "messages"
    assert result.confidence == 0.92
