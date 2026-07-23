import pytest
from sqlalchemy import create_engine

from pulse.chat.admin_tools import DEFAULT_ROUTER
from pulse.chat.planner import _plan_with_rules
from pulse.config import AppConfig, TenantConfig
from pulse.storage.models import Base, Member
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def owner_member():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    team, repo = make_team_repo(session)
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin1", display_name="Admin", password="x"
    )
    repo.commit()
    return owner


def test_plan_nudge_with_permission(owner_member):
    plans = _plan_with_rules("帮我催一下没交的人", owner_member, DEFAULT_ROUTER)
    assert any(p[0] == "nudge_unsubmitted" for p in plans)


def test_plan_denied_without_portal():
    member = Member(
        team_id="t1",
        dingtalk_user_id="u1",
        display_name="User",
        status="active",
        portal_role=None,
    )
    plans = _plan_with_rules("催一下没交的", member, DEFAULT_ROUTER)
    assert plans == []


def test_admin_tool_router_still_has_nudge(owner_member):
    assert "nudge_unsubmitted" in DEFAULT_ROUTER._tools
    assert "list_pending_reviews" not in DEFAULT_ROUTER._tools
    assert "confirm_ingestion" not in DEFAULT_ROUTER._tools
