from __future__ import annotations

import pytest
from sqlalchemy import select

from pulse.storage.db import init_db
from pulse.storage.models import KnowledgeEntry, Member
from pulse.tool_center.knowledge import KnowledgeService, extract_tip_body, looks_like_tip, organize_tip
from pulse.tool_center.seed import seed_v2_catalog
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


def test_looks_like_tip_and_extract():
    assert looks_like_tip("心得：本月用 Composer 做批量重命名很顺手")
    assert not looks_like_tip("短")
    body = extract_tip_body("技巧：先用 @file 再提问")
    assert body == "先用 @file 再提问"


def test_organize_tip_rules_fallback():
    data = organize_tip("Cursor Tab 补全配合小步提交", config=None)
    assert data["title"]
    assert "cursor" in data["tags"]


def test_knowledge_create_list_digest(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    session.flush()
    author = _member(session, team.id)

    svc = KnowledgeService(session, team.id)
    entry = svc.create_from_raw(
        author=author,
        raw_text="心得：用 Cursor 的 @codebase 问架构问题",
        source_channel="dingtalk_dm",
        period="2026-06",
    )
    session.flush()

    assert entry.title
    assert entry.period == "2026-06"
    assert entry.author_member_id == author.id

    rows = svc.list_entries(period="2026-06")
    assert len(rows) == 1

    digest = svc.build_monthly_digest("2026-06")
    assert "2026-06 AI 使用技巧精选" in digest
    assert entry.title in digest


def test_knowledge_pin_and_hide(session):
    team, _ = make_team_repo(session)
    author = _member(session, team.id)
    svc = KnowledgeService(session, team.id)
    entry = svc.create_from_raw(
        author=author,
        raw_text="心得：测试置顶与隐藏",
        source_channel="web",
        period="2026-06",
    )
    session.flush()

    svc.update_entry(entry.id, pinned=True)
    listed = svc.list_entries(period="2026-06")
    assert listed[0].pinned is True

    svc.update_entry(entry.id, status="hidden")
    assert svc.list_entries(period="2026-06") == []
    assert len(svc.list_entries(period="2026-06", include_hidden=True)) == 1


def test_knowledge_entry_persisted(session):
    team, _ = make_team_repo(session)
    author = _member(session, team.id)
    svc = KnowledgeService(session, team.id)
    svc.create_from_raw(
        author=author,
        raw_text="心得：持久化测试",
        source_channel="web",
        period="2026-06",
    )
    session.commit()

    row = session.scalar(select(KnowledgeEntry))
    assert row is not None
    assert row.team_id == team.id
    assert row.status == "published"
