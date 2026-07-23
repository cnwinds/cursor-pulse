import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.storage.models import Base, Member, UsageIngestion
from pulse.web.portal import (
    PortalAdminError,
    bootstrap_portal_owner,
    delete_member_without_ingestions,
    revoke_portal_access,
)
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = factory()
    yield s, make_team_repo(s)[0].id
    s.close()


def test_revoke_portal_access(session):
    s, team_id = session
    _team, repo = make_team_repo(s)
    member = bootstrap_portal_owner(
        repo, dingtalk_user_id="u1", display_name="Alice", password="secret1234"
    )
    repo.commit()

    revoked = revoke_portal_access(s, team_id, "u1")
    s.commit()

    assert revoked.portal_role is None
    assert revoked.password_hash is None
    assert revoked.last_portal_login_at is None


def test_revoke_unknown_member(session):
    s, team_id = session
    make_team_repo(s)
    with pytest.raises(PortalAdminError, match="未找到成员"):
        revoke_portal_access(s, team_id, "missing")


def test_revoke_no_portal_access(session):
    s, team_id = session
    _team, repo = make_team_repo(s)
    repo.add_member("u2", "Bob")
    repo.commit()
    with pytest.raises(PortalAdminError, match="无后台权限"):
        revoke_portal_access(s, team_id, "u2")


def test_delete_member_without_ingestions(session):
    s, team_id = session
    _team, repo = make_team_repo(s)
    repo.add_member("u3", "Carol")
    repo.commit()

    deleted = delete_member_without_ingestions(s, team_id, "u3")
    s.commit()

    assert deleted.dingtalk_user_id == "u3"
    assert s.scalar(select(Member).where(Member.dingtalk_user_id == "u3")) is None


def test_delete_member_with_ingestions(session):
    s, team_id = session
    _team, repo = make_team_repo(s)
    member = repo.add_member("u4", "Dave")
    s.add(
        UsageIngestion(
            member_id=member.id,
            billing_period="2026-06",
            source_type="manual_csv",
            channel="private",
            status="confirmed",
            triggered_by=member.id,
        )
    )
    repo.commit()

    with pytest.raises(PortalAdminError, match="摄取记录"):
        delete_member_without_ingestions(s, team_id, "u4")
