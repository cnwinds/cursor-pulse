from __future__ import annotations

from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_auto_created_member_is_pending_not_nudged():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    _team, repo = make_team_repo(session)

    member = repo.get_or_create_member("u_auto", "Auto User")
    repo.commit()

    assert member.status == "pending"
    assert repo.list_active_members() == []
    assert repo.get_unsubmitted_members("2026-06") == []


def test_add_member_marks_active_for_nudges():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    _team, repo = make_team_repo(session)

    member = repo.add_member("u1", "Alice")
    repo.commit()

    assert member.status == "active"
    unsubmitted = repo.get_unsubmitted_members("2026-06")
    assert len(unsubmitted) == 1
    assert unsubmitted[0].display_name == "Alice"


def test_get_or_create_member_does_not_clobber_name_with_dingtalk_id():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    _team, repo = make_team_repo(session)

    member = repo.add_member("1584929783723323", "熊波")
    repo.commit()

    again = repo.get_or_create_member("1584929783723323", "1584929783723323")
    repo.commit()

    assert again.id == member.id
    assert again.display_name == "熊波"


def test_get_or_create_member_upgrades_placeholder_name_from_nick():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    _team, repo = make_team_repo(session)

    member = repo.get_or_create_member("1584929783723323", "1584929783723323")
    repo.commit()
    assert member.display_name == "1584929783723323"

    upgraded = repo.get_or_create_member("1584929783723323", "熊波")
    repo.commit()
    assert upgraded.display_name == "熊波"
