from __future__ import annotations

import pytest
from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.invoke import invoke_capability
from pulse.config import load_config
from pulse.storage.db import init_db
from pulse.storage.models import KnowledgeEntry, Member
from pulse.tool_center.knowledge import KnowledgeService, TipSubmissionError, evaluate_tip_submission
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


def _good_body() -> str:
    return """## 技巧说明
在 Cursor 中批量重命名文件时，用 Composer 可以一次处理多个文件。

## 操作步骤
1. 先选中目标目录，打开 Composer
2. 输入重命名规则，建议先小范围试一条
3. 确认 diff 后再应用全部变更

## 注意事项
- 建议先提交 git 再批量改名"""


def test_evaluate_tip_rejects_short_body():
    result = evaluate_tip_submission("批量重命名", "太短了", config=None)
    assert result["approved"] is False
    assert "正文太短" in result["feedback"]


def test_evaluate_tip_rejects_missing_structure():
    body = "这是一个很好的技巧，大家可以多用用，效率会提升很多。" * 3
    result = evaluate_tip_submission("泛泛技巧", body, config=None)
    assert result["approved"] is False


def test_evaluate_tip_accepts_structured_body():
    result = evaluate_tip_submission("Composer 批量重命名", _good_body(), config=None)
    assert result["approved"] is True


def test_create_from_submission_requires_quality(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    author = _member(session, team.id)
    svc = KnowledgeService(session, team.id)

    with pytest.raises(TipSubmissionError):
        svc.create_from_submission(
            author=author,
            title="短",
            body="太短",
            source_channel="test",
            period="2026-06",
        )

    entry = svc.create_from_submission(
        author=author,
        title="Composer 批量重命名",
        body=_good_body(),
        source_channel="test",
        period="2026-06",
        tags=["cursor"],
    )
    assert entry.title == "Composer 批量重命名"
    assert "## 操作步骤" in entry.body


def _request(*, team_id: str, actor_member_id: str, capability_key: str, arguments: dict | None = None):
    return CapabilityInvokeRequest(
        invocation_id="inv-1",
        idempotency_key="idem-1",
        team_id=team_id,
        actor_member_id=actor_member_id,
        capability_key=capability_key,
        capability_version="1",
        arguments=arguments or {},
    )


def test_knowledge_tip_list_and_read_handlers(session):
    team, _ = make_team_repo(session)
    seed_v2_catalog(session, team)
    author = _member(session, team.id)
    svc = KnowledgeService(session, team.id)
    entry = svc.create_from_submission(
        author=author,
        title="测试技巧标题",
        body=_good_body(),
        source_channel="test",
        period="2026-06",
        skip_evaluation=True,
    )
    session.flush()

    config = load_config("config.yaml")

    list_req = _request(
        team_id=team.id,
        actor_member_id=author.id,
        capability_key="knowledge.tip.list",
    )
    list_res = invoke_capability(session, request=list_req, config=config)
    assert list_res.status == "succeeded"
    titles = [e["title"] for e in (list_res.result or {}).get("entries") or []]
    assert entry.title in titles

    read_req = _request(
        team_id=team.id,
        actor_member_id=author.id,
        capability_key="knowledge.tip.read",
        arguments={"title_query": "测试技巧"},
    )
    read_res = invoke_capability(session, request=read_req, config=config)
    assert read_res.status == "succeeded"
    assert (read_res.result or {}).get("title") == "测试技巧标题"
    assert "## 操作步骤" in ((read_res.result or {}).get("body") or "")


def test_knowledge_tip_create_rejects_low_quality(session):
    team, _ = make_team_repo(session)
    author = _member(session, team.id)
    config = load_config("config.yaml")

    req = _request(
        team_id=team.id,
        actor_member_id=author.id,
        capability_key="knowledge.tip.create",
        arguments={"title": "x", "body": "太短"},
    )
    res = invoke_capability(session, request=req, config=config)
    assert res.status == "failed"
    assert res.error_code == "tip_quality_rejected"
    assert session.scalar(select(KnowledgeEntry)) is None
