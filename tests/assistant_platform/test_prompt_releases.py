from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.prompts.models import PromptFragmentRow, PromptReleaseRow
from assistant_platform.prompts.fragments import AGENT_TOOLS_RELEASE_NAME, PERSONA_ONLY_RELEASE_NAME
from assistant_platform.prompts.seed import (
    ensure_production_prompt_release,
    get_production_release,
    production_needs_agent_tools_upgrade,
    production_needs_persona_only_upgrade,
    seed_default_prompt_release,
    upgrade_production_to_agent_tools,
    upgrade_production_to_persona_only,
)
from assistant_platform.storage.db import init_assistant_db, make_engine
from assistant_platform.storage.migrate import migrate_assistant_schema
from assistant_platform.storage.models import Base

TEAM_ID = "team-prompts"


def _event(*, msg_id: str = "m-prompt") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted="hello",
        occurred_at=datetime.now(timezone.utc),
    )


def test_init_does_not_seed_production_prompt_release():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()

    release = get_production_release(session)
    assert release is None

    fragments = session.scalars(select(PromptFragmentRow)).all()
    assert fragments == []
    session.close()


def test_new_session_does_not_require_prompt_release():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()

    session_row, _ = attach_user_message(session, _event(msg_id="m-new"))
    session.commit()

    assert session_row.prompt_release_id is None
    session.close()


def test_continued_session_keeps_original_prompt_release_id():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()

    first_session, _ = attach_user_message(session, _event(msg_id="m-a"))
    first_release_id = first_session.prompt_release_id
    session.commit()

    continued, _ = attach_user_message(session, _event(msg_id="m-b"))
    session.commit()

    assert continued.id == first_session.id
    assert continued.prompt_release_id == first_release_id
    assert continued.prompt_release_id is None
    session.close()


def test_seed_default_prompt_release_does_not_create_on_empty_db():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    assert seed_default_prompt_release(session) is None
    session.commit()

    releases = session.scalars(select(PromptReleaseRow)).all()
    assert releases == []
    session.close()


def _empty_prompt_session():
    import assistant_platform.prompts.models  # noqa: F401 — register tables

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    migrate_assistant_schema(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()


@pytest.mark.skip(reason="prompt release pipeline retired")
def test_auto_upgrade_legacy_v1_production_to_agent_tools():
    session = _empty_prompt_session()

    legacy_precepts = PromptFragmentRow(
        key="precepts.md",
        content=(
            "戒律：\n"
            "1. 不泄露密钥与隐私。\n"
            "2. 不确定时先澄清。\n"
            "3. 优先给出可执行步骤（如具体命令格式）。\n"
        ),
        version="1",
        status="active",
    )
    legacy_heart = PromptFragmentRow(
        key="heart.md",
        content="你是小脉。",
        version="1",
        status="active",
    )
    session.add_all([legacy_precepts, legacy_heart])
    session.flush()

    v1 = PromptReleaseRow(
        name="v1",
        status="production",
        fragment_ids_json=[legacy_heart.id, legacy_precepts.id],
    )
    session.add(v1)
    session.commit()

    assert production_needs_agent_tools_upgrade(session) is True

    upgraded = upgrade_production_to_agent_tools(session)
    session.commit()

    assert upgraded is not None
    assert upgraded.name == AGENT_TOOLS_RELEASE_NAME
    assert upgraded.status == "production"

    retired_v1 = session.scalar(select(PromptReleaseRow).where(PromptReleaseRow.name == "v1"))
    assert retired_v1 is not None
    assert retired_v1.status == "retired"

    fragments = session.scalars(select(PromptFragmentRow)).all()
    new_precepts = [f for f in fragments if f.key == "precepts.md" and "人设与表达" in f.content]
    assert new_precepts
    session.close()


@pytest.mark.skip(reason="prompt release pipeline retired")
def test_auto_upgrade_v2_production_to_persona_only():
    session = _empty_prompt_session()

    v2_precepts = PromptFragmentRow(
        key="precepts.md",
        content=(
            "戒律：\n"
            "4. 标记为高风险的 tool：先用自然语言说明将执行的操作，"
            "等用户明确同意后再调用。\n"
            "6. 简单本人用量/额度优先 usage_self_read、quota_self_read；"
            "开放分析、对比、排名类问题再用 usage_query。\n"
        ),
        version="2",
        status="active",
    )
    v2_heart = PromptFragmentRow(
        key="heart.md",
        content="你是小脉，团队内的 Cursor 使用助手。",
        version="2",
        status="active",
    )
    session.add_all([v2_precepts, v2_heart])
    session.flush()

    v2 = PromptReleaseRow(
        name=AGENT_TOOLS_RELEASE_NAME,
        status="production",
        fragment_ids_json=[v2_heart.id, v2_precepts.id],
    )
    session.add(v2)
    session.commit()

    assert production_needs_persona_only_upgrade(session) is True

    upgraded = upgrade_production_to_persona_only(session)
    session.commit()

    assert upgraded is not None
    assert upgraded.name == PERSONA_ONLY_RELEASE_NAME
    assert upgraded.status == "production"

    retired_v2 = session.scalar(
        select(PromptReleaseRow).where(PromptReleaseRow.name == AGENT_TOOLS_RELEASE_NAME)
    )
    assert retired_v2 is not None
    assert retired_v2.status == "retired"
    session.close()


@pytest.mark.skip(reason="prompt release pipeline retired")
def test_ensure_production_prompt_release_is_idempotent_after_upgrade():
    session = _empty_prompt_session()

    legacy = PromptFragmentRow(
        key="precepts.md",
        content="优先给出可执行步骤（如具体命令格式）。",
        version="1",
        status="active",
    )
    session.add(legacy)
    session.flush()
    v1 = PromptReleaseRow(
        name="v1",
        status="production",
        fragment_ids_json=[legacy.id],
    )
    session.add(v1)
    session.commit()

    ensure_production_prompt_release(session)
    session.commit()
    first = get_production_release(session)
    assert first is not None
    assert first.name == PERSONA_ONLY_RELEASE_NAME

    ensure_production_prompt_release(session)
    session.commit()
    second = get_production_release(session)
    assert second is not None
    assert second.id == first.id

    persona_releases = session.scalars(
        select(PromptReleaseRow).where(PromptReleaseRow.name == PERSONA_ONLY_RELEASE_NAME)
    ).all()
    assert len(persona_releases) == 1
    session.close()
