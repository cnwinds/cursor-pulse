"""智谱团队版额度请求形状与 GLM 套餐同步 history（M2）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from pulse.ingestion.coding_plan_sync import CodingPlanQuotaSyncService
from pulse.ingestion.credentials import CredentialService
from pulse.integrations.coding_plan.types import CodingPlanQuotaResult
from pulse.integrations.coding_plan.zhipu import (
    _ZHIPU_TEAM_QUOTA_URL,
    fetch_glm_quota,
    fetch_zhipu_team_quota,
    parse_zhipu_token_tiers,
)
from pulse.storage.models import AiAccountPlanHistory, AiPlan, AiVendor, Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
import pytest
from pulse.storage.db import init_db
from tests.conftest import make_team_repo

FIXTURE_DATA = json.loads((Path(__file__).parent / "fixtures/coding_plan/zhipu_two_tiers.json").read_text())["data"]
FIXTURE_BODY = {"success": True, "data": FIXTURE_DATA}
ENC_KEY = "test-encryption-key-32bytes-long!!"


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_fetch_zhipu_team_sends_type2_and_org_headers():
    captured: dict = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        resp.is_success = True
        resp.json.return_value = FIXTURE_BODY
        return resp

    mock_client = MagicMock()
    mock_client.get.side_effect = fake_get
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False

    with patch("pulse.integrations.coding_plan.zhipu.outbound_client", return_value=mock_cm):
        result = fetch_zhipu_team_quota("team-key", organization_id="org-xxx", project_id="proj_yyy")

    assert captured["url"] == _ZHIPU_TEAM_QUOTA_URL
    assert "?type=2" in captured["url"]
    assert captured["headers"]["Authorization"] == "team-key"
    assert captured["headers"]["bigmodel-organization"] == "org-xxx"
    assert captured["headers"]["bigmodel-project"] == "proj_yyy"
    assert len(result.tiers) >= 1


def test_fetch_glm_quota_team_requires_bigmodel():
    with pytest.raises(ValueError, match="国内站"):
        fetch_glm_quota(
            "k",
            region="zai",
            organization_id="org",
            project_id="proj",
        )


def test_glm_sync_updates_plan_history(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    tool_repo = ToolCenterRepository(session, team.id)
    vendor = session.scalar(select(AiVendor).where(AiVendor.slug == "glm"))
    assert vendor
    lite = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id, AiPlan.slug == "lite"))
    pro = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id, AiPlan.slug == "pro"))
    assert lite and pro

    member = Member(team_id=team.id, display_name="T", channel_user_id="glm-m2", status="active")
    session.add(member)
    session.flush()

    account = tool_repo.create_account(
        vendor_id=vendor.id,
        plan_id=lite.id,
        account_identifier="team-glm@example.com",
        api_region="bigmodel",
        glm_organization_id="org-1",
        glm_project_id="proj-1",
    )
    session.commit()

    mock_result = CodingPlanQuotaResult(
        plan_level="pro",
        tiers=parse_zhipu_token_tiers(FIXTURE_DATA),
        extras=[],
    )

    with (
        patch("pulse.integrations.coding_plan.fetch_glm_quota", return_value=mock_result),
        patch("pulse.ingestion.coding_plan_sync.fetch_glm_quota", return_value=mock_result),
    ):
        cred_svc = CredentialService(session, ENC_KEY)
        cred_svc.bind_coding_plan_api_key(
            account_id=account.id,
            api_key="glm-test-key",
            member_id=member.id,
        )
        session.commit()

        svc = CodingPlanQuotaSyncService(session, ENC_KEY)
        svc.sync_account(account.id, member_id=member.id)

    session.refresh(account)
    assert account.plan_id == pro.id
    rows = session.scalars(select(AiAccountPlanHistory).where(AiAccountPlanHistory.account_id == account.id)).all()
    assert len(rows) >= 2
    assert any(r.plan_id == pro.id and r.note and "GLM API" in r.note for r in rows)
