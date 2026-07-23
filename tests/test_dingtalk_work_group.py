import asyncio
import pytest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.channels.dingtalk.handler import DingTalkChannelHandler
from pulse.channels.dingtalk.work_group import (
    activate_work_group,
    build_work_group_welcome_message,
    is_work_group_activation,
    persist_work_group_binding,
)
from pulse.config import AppConfig, DingTalkConfig, AssistantMirrorConfig, TenantConfig
from pulse.storage.db import init_db
from pulse.storage.models import Base, TeamSetting
from tests.conftest import make_team_repo


def test_is_work_group_activation():
    assert is_work_group_activation("@小脉 启动") is False  # normalized before call
    assert is_work_group_activation("启动")
    assert is_work_group_activation("激活")
    assert is_work_group_activation("start")
    assert is_work_group_activation("帮助") is False


def test_activate_work_group_persists_team_settings(tmp_path):
    db_path = tmp_path / "pulse.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = sf()
    team, repo = make_team_repo(session)
    member = repo.get_or_create_member("admin1", "Admin")
    session.commit()

    config = AppConfig(tenant=TenantConfig(slug="test", name="Test"))
    incoming = MagicMock()
    incoming.conversation_id = "cid-work-group=="
    incoming.conversation_title = "AI 用量群"

    result = activate_work_group(
        config,
        session,
        team_id=team.id,
        incoming=incoming,
        user_id="admin1",
        member_id=member.id,
        member_portal_role="owner",
    )
    session.commit()

    assert result.handled is True
    assert result.binding_changed is True
    assert "工作群" in (result.reply or "")
    assert config.dingtalk.group_open_conversation_id == "cid-work-group=="

    row = session.scalar(
        __import__("sqlalchemy").select(TeamSetting).where(
            TeamSetting.team_id == team.id,
            TeamSetting.section == "dingtalk",
        )
    )
    assert row is not None
    assert row.data["group_open_conversation_id"] == "cid-work-group=="
    assert row.data.get("group_title") == "AI 用量群"
    session.close()


def test_rebind_requires_admin(tmp_path):
    db_path = tmp_path / "pulse.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = sf()
    team, repo = make_team_repo(session)
    member = repo.get_or_create_member("u1", "Bob")
    session.commit()

    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        dingtalk=DingTalkConfig(group_open_conversation_id="cid-old=="),
    )
    incoming = MagicMock()
    incoming.conversation_id = "cid-new=="
    incoming.conversation_title = "新群"

    result = activate_work_group(
        config,
        session,
        team_id=team.id,
        incoming=incoming,
        user_id="u1",
        member_id=member.id,
        member_portal_role="member",
    )
    assert result.handled is True
    assert "管理员" in (result.reply or "")
    assert config.dingtalk.group_open_conversation_id == "cid-old=="
    session.close()


def test_handler_activation_replies_without_mirror():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        ),
    )
    session = init_db("sqlite:///:memory:")()
    _team, repo = make_team_repo(session)
    repo.get_or_create_member("admin1", "Admin")
    session.commit()

    handler = DingTalkChannelHandler(
        config=config,
        session_factory=lambda: session,
        messenger=MagicMock(),
    )
    incoming = MagicMock()
    incoming.conversation_type = "2"
    incoming.is_in_at_list = True
    incoming.sender_staff_id = "admin1"
    incoming.sender_id = "admin1"
    incoming.sender_nick = "Admin"
    incoming.conversation_id = "cid-activate=="
    incoming.conversation_title = "测试群"
    incoming.message_id = "msg-1"
    incoming.text.content = "@小脉 启动"
    incoming.message_type = "text"

    async def run():
        with (
            patch("pulse.channels.dingtalk.mirror.mirror_dingtalk_message") as mirror,
            patch.object(handler, "reply_text") as reply,
        ):
            await handler._handle_message(incoming, {})
            mirror.assert_not_called()
            reply.assert_called_once()
            welcome = reply.call_args[0][0]
            assert "工作群" in welcome
            assert "私聊" in welcome

    asyncio.run(run())

    assert config.dingtalk.group_open_conversation_id == "cid-activate=="

    row = session.scalar(
        __import__("sqlalchemy").select(TeamSetting).where(
            TeamSetting.section == "dingtalk",
        )
    )
    assert row is not None
    assert row.data["group_open_conversation_id"] == "cid-activate=="
