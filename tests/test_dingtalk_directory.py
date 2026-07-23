import pytest

from pulse.integrations.dingtalk_directory import (
    list_directory_tree_children,
    search_directory_by_name,
    sync_dingtalk_directory,
)
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


class FakeDirectoryClient:
    def __init__(self):
        self.departments = {
            1: {"name": "绿岸网络"},
            10: {"name": "研发部"},
            11: {"name": "产品部"},
        }
        self.users_by_dept = {
            1: [{"userid": "u_root", "name": "许帆"}],
            10: [{"userid": "u_dev", "name": "许帆"}, {"userid": "u_boss", "name": "张主管"}],
            11: [{"userid": "u_pm", "name": "李产品"}],
        }
        self.sub_departments = {
            1: [{"dept_id": 10, "name": "研发部"}, {"dept_id": 11, "name": "产品部"}],
            10: [],
            11: [],
        }
        self.user_details = {
            "u_dev": {"manager_userid": "u_boss"},
        }

    def get_department(self, dept_id: int):
        return self.departments.get(dept_id, {"name": str(dept_id)})

    def list_users_in_dept(self, dept_id: int):
        return list(self.users_by_dept.get(dept_id, []))

    def list_sub_departments(self, dept_id: int):
        return list(self.sub_departments.get(dept_id, []))

    def get_user(self, userid: str):
        return self.user_details.get(userid, {})


def test_search_directory_by_name_partial_match():
    client = FakeDirectoryClient()
    results = search_directory_by_name(client, "许帆", root_dept_id=1)
    assert len(results) == 2
    dept_names = {r["department_name"] for r in results}
    assert dept_names == {"绿岸网络", "研发部"}


def test_search_directory_finds_user_in_sub_department():
    client = FakeDirectoryClient()
    results = search_directory_by_name(client, "李产品", root_dept_id=1)
    assert len(results) == 1
    assert results[0]["department_name"] == "产品部"


def test_list_directory_tree_children(session):
    team, repo = make_team_repo(session)
    client = FakeDirectoryClient()

    tree = list_directory_tree_children(repo, client, 1)
    assert tree["label"] == "绿岸网络"
    child_types = {c["type"] for c in tree["children"]}
    assert child_types == {"department", "user"}

    dept_nodes = [c for c in tree["children"] if c["type"] == "department"]
    assert {n["label"] for n in dept_nodes} == {"研发部", "产品部"}

    user_node = next(c for c in tree["children"] if c["type"] == "user")
    assert user_node["label"] == "许帆"
    assert user_node["member_id"]


def test_sync_dingtalk_directory_walks_sub_departments(session):
    from pulse.config import load_config

    team, repo = make_team_repo(session)
    config = load_config("config.yaml")
    stats = sync_dingtalk_directory(repo, config, client=FakeDirectoryClient())

    assert stats["fetched"] == 4
    dev = repo.get_member_by_dingtalk_id("u_dev")
    pm = repo.get_member_by_dingtalk_id("u_pm")
    assert dev is not None
    assert dev.department_name == "研发部"
    assert pm is not None
    assert pm.department_name == "产品部"
