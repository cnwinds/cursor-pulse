from __future__ import annotations

from unittest.mock import MagicMock

from pulse.channels.inbound import InboundMessage, dispatch_text_command
from pulse.config import AppConfig, CredentialConfig, TenantConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def _config() -> AppConfig:
    return AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key="0" * 32),
    )


def _inbound(
    text: str,
    *,
    channel: str = "dingtalk",
    channel_user_id: str = "u1",
    display_name: str = "Alice",
    conversation_type: str = "oto",
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        channel_user_id=channel_user_id,
        display_name=display_name,
        text=text,
        conversation_type=conversation_type,
        conversation_id=channel_user_id,
        message_id="msg-1",
    )


def test_dispatch_text_command_returns_none_for_empty_text():
    session = init_db("sqlite:///:memory:")()
    try:
        reply = dispatch_text_command(
            config=_config(),
            session_factory=lambda: session,
            messenger=MagicMock(),
            inbound=_inbound("   "),
        )
        assert reply is None
    finally:
        session.close()


def test_dispatch_text_command_returns_none_for_unrecognized_text():
    session = init_db("sqlite:///:memory:")()
    try:
        reply = dispatch_text_command(
            config=_config(),
            session_factory=lambda: session,
            messenger=MagicMock(),
            inbound=_inbound("查询 我的用量"),
        )
        assert reply is None
    finally:
        session.close()


def test_dispatch_text_command_handles_quota_for_new_member():
    session = init_db("sqlite:///:memory:")()
    try:
        reply = dispatch_text_command(
            config=_config(),
            session_factory=lambda: session,
            messenger=MagicMock(),
            inbound=_inbound("额度"),
        )
        assert reply == "尚未绑定 Cursor 账号"
    finally:
        session.close()


def test_dispatch_text_command_handles_help():
    session = init_db("sqlite:///:memory:")()
    try:
        reply = dispatch_text_command(
            config=_config(),
            session_factory=lambda: session,
            messenger=MagicMock(),
            inbound=_inbound("帮助"),
        )
        assert isinstance(reply, str)
        assert reply.strip()
    finally:
        session.close()


def test_dispatch_text_command_is_channel_neutral_for_feishu():
    """Same command path works for a non-DingTalk channel_user_id/channel."""
    session = init_db("sqlite:///:memory:")()
    try:
        reply = dispatch_text_command(
            config=_config(),
            session_factory=lambda: session,
            messenger=MagicMock(),
            inbound=_inbound(
                "额度", channel="feishu", channel_user_id="ou_feishu_user_1"
            ),
        )
        assert reply == "尚未绑定 Cursor 账号"
    finally:
        session.close()


def test_dispatch_text_command_key_loan_return_for_existing_member_without_loan():
    session = init_db("sqlite:///:memory:")()
    try:
        _team, repo = make_team_repo(session, slug="test")
        repo.get_or_create_member("u1", "Alice")
        repo.commit()

        reply = dispatch_text_command(
            config=_config(),
            session_factory=lambda: session,
            messenger=MagicMock(),
            inbound=_inbound("归还 Key"),
        )
        assert reply == "你当前没有可归还的借用。"
    finally:
        session.close()
