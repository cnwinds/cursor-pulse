from __future__ import annotations

from pulse.config import load_config
from pulse.integrations.dingtalk_directory import sync_dingtalk_directory
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_dingtalk_directory_sync_mock():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    try:
        team, repo = make_team_repo(session)

        class FakeClient:
            def get_department(self, dept_id: int):
                return {"name": "研发部"}

            def list_sub_departments(self, dept_id: int):
                return []

            def list_users_in_dept(self, dept_id: int):
                return [
                    {"userid": "u1", "name": "Alice"},
                    {"userid": "u2", "name": "Boss"},
                ]

            def get_user(self, userid: str):
                if userid == "u1":
                    return {"manager_userid": "u2"}
                return {}

        config = load_config("config.yaml")
        stats = sync_dingtalk_directory(repo, config, client=FakeClient())
        assert stats["fetched"] == 2
        alice = repo.get_member_by_dingtalk_id("u1")
        boss = repo.get_member_by_dingtalk_id("u2")
        assert alice is not None
        assert alice.department_name == "研发部"
        assert alice.manager_member_id == boss.id
    finally:
        session.close()
