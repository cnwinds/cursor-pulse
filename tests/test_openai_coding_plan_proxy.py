"""M4 OpenAI Coding Plan gateway."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import pytest
from pulse.ingestion.crypto import encrypt_secret
from pulse.openai_proxy.authorize import authorize_pkcp
from pulse.openai_proxy.pool import list_cp_pool_entries, pick_cp_credential
from pulse.openai_proxy.upstream import openai_base_url
from pulse.proxy.key_crud import create_coding_plan_key
from pulse.storage.db import init_db
from pulse.storage.models import AiAccount, AiAccountCredential, AiPlan, AiVendor, Member, Team
from pulse.tool_center.seed import seed_v2_catalog
from sqlalchemy import select
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_openai_base_url_glm_regions():
    assert openai_base_url(vendor_slug="glm", api_region="zai").startswith("https://api.z.ai/")
    assert "coding" in openai_base_url(vendor_slug="glm", api_region="bigmodel")
    assert openai_base_url(vendor_slug="minimax", api_region="cn").startswith("https://api.minimaxi.com/")


def test_pkcp_authorize(session):
    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m1", display_name="M1")
    session.add(member)
    seed_v2_catalog(session, team)
    session.flush()
    key, plain = create_coding_plan_key(
        session,
        name="glm pool",
        member_id=member.id,
        coding_plan_vendor="glm",
        encryption_key=TEST_KEY,
    )
    session.commit()
    ok = authorize_pkcp(session, plain)
    assert ok["status"] == "ok"
    assert ok["coding_plan_vendor"] == "glm"
    bad = authorize_pkcp(session, "pk_deadbeef")
    assert bad["status"] == "invalid"


def test_cp_pool_picks_enabled_account(session):
    team, _repo = make_team_repo(session)
    member = Member(team_id=team.id, channel_user_id="m2", display_name="M2")
    session.add(member)
    seed_v2_catalog(session, team)
    vendor = session.scalar(select(AiVendor).where(AiVendor.slug == "glm"))
    plan = session.scalar(select(AiPlan).where(AiPlan.vendor_id == vendor.id))
    acc = AiAccount(
        team_id=team.id,
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier="glm-pool@test",
        api_region="zai",
        cp_proxy_enabled=True,
    )
    session.add(acc)
    session.flush()
    session.add(
        AiAccountCredential(
            account_id=acc.id,
            vendor_id=vendor.id,
            credential_type="coding_plan_api_key",
            encrypted_value=encrypt_secret("glm-test-key", TEST_KEY),
            key_hint="glm…key",
            key_role="primary",
            bound_by_member_id=member.id,
            last_sync_status="success",
            last_sync_at=datetime.now(UTC),
        )
    )
    session.commit()
    entries = list_cp_pool_entries(session, vendor_slug="glm", encryption_key=TEST_KEY, include_api_keys=True)
    assert len(entries) == 1
    picked = pick_cp_credential(session, vendor_slug="glm", encryption_key=TEST_KEY)
    assert picked and picked["api_key"] == "glm-test-key"
