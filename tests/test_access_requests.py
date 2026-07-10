from __future__ import annotations

import pytest

from pulse.storage.db import init_db
from pulse.storage.models import Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.requests import AccessRequestError, AccessRequestService
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def _applicant(session, team_id, name="Alice", manager_id=None):
    m = Member(
        team_id=team_id,
        dingtalk_user_id=f"u-{name}",
        display_name=name,
        status="active",
        manager_member_id=manager_id,
    )
    session.add(m)
    session.flush()
    return m


def test_access_request_flow_assign_trial(session):
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    manager = _applicant(session, team.id, "Boss")
    applicant = _applicant(session, team.id, "Alice", manager_id=manager.id)

    tool_repo = ToolCenterRepository(session, team.id)
    vendor = tool_repo.get_vendor_by_slug("cursor")
    assert vendor

    svc = AccessRequestService(session, team.id)
    row = svc.create_draft(applicant=applicant, vendor_id=vendor.id, reason="需要写代码")
    svc.submit(row.id, applicant)
    action = svc.approve(row.id, manager, is_admin=False)
    assert action.request.status == "approved"

    assign = svc.assign_trial(row.id)
    assert assign.request.status == "trial_assigned"
    assert assign.request.assigned_account_id

    account = tool_repo.get_account(assign.request.assigned_account_id)
    assert account is not None
    assert account.primary_member_id == applicant.id


def test_access_request_reject_non_manager(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()

    manager = _applicant(session, team.id, "Boss")
    applicant = _applicant(session, team.id, "Alice", manager_id=manager.id)
    other = _applicant(session, team.id, "Bob")

    tool_repo = ToolCenterRepository(session, team.id)
    vendor = tool_repo.get_vendor_by_slug("cursor")
    svc = AccessRequestService(session, team.id)
    row = svc.create_draft(applicant=applicant, vendor_id=vendor.id)
    svc.submit(row.id, applicant)

    with pytest.raises(AccessRequestError, match="仅直属主管"):
        svc.approve(row.id, other, is_admin=False)


def test_dingtalk_directory_sync_mock(session):
    from pulse.integrations.dingtalk_directory import sync_dingtalk_directory

    team, repo = make_team_repo(session)

    class FakeClient:
        def get_department(self, dept_id: int):
            return {"name": "研发部"}

        def list_users_in_dept(self, dept_id: int):
            return [
                {"userid": "u1", "name": "Alice"},
                {"userid": "u2", "name": "Boss"},
            ]

        def get_user(self, userid: str):
            if userid == "u1":
                return {"manager_userid": "u2"}
            return {}

    from pulse.config import load_config

    config = load_config("config.yaml")
    stats = sync_dingtalk_directory(repo, config, client=FakeClient())
    assert stats["fetched"] == 2
    alice = repo.get_member_by_dingtalk_id("u1")
    boss = repo.get_member_by_dingtalk_id("u2")
    assert alice is not None
    assert alice.department_name == "研发部"
    assert alice.manager_member_id == boss.id
