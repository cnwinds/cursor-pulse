from __future__ import annotations

from pulse.channels.base import NullMessenger
from pulse.channels.commands_common import channel_member
from pulse.channels.inbound import InboundMessage, dispatch_text_command
from pulse.channels.reminders.scheduler import _send_ok
from pulse.config import AppConfig, BotPlatformConfig
from pulse.storage.db import init_db
from pulse.web.portal import get_team_member, identity_channel_for_config, sync_portal_owners_from_config
from tests.conftest import make_team_repo


def test_identity_channel_for_config():
    assert identity_channel_for_config("none") == "web"
    assert identity_channel_for_config("dingtalk") == "dingtalk"
    assert identity_channel_for_config("feishu") == "feishu"


def test_sync_portal_owners_uses_bot_channel(tmp_path):
    session = init_db(f"sqlite:///{tmp_path / 'id.db'}")()
    team, _repo = make_team_repo(session)
    n = sync_portal_owners_from_config(
        session, team.id, ["im-admin-1"], channel="dingtalk"
    )
    session.flush()
    assert n == 1
    member = get_team_member(session, team.id, "im-admin-1", channel="dingtalk")
    assert member is not None
    assert member.channel == "dingtalk"
    assert member.portal_role == "owner"
    # Second sync with web must not create a duplicate web row for same userid
    # when dingtalk row already exists (falls back to existing).
    n2 = sync_portal_owners_from_config(session, team.id, ["im-admin-1"], channel="web")
    session.flush()
    assert n2 == 0
    assert get_team_member(session, team.id, "im-admin-1", channel="web") is None
    session.close()


def test_get_team_member_prefers_deterministic_channel(tmp_path):
    session = init_db(f"sqlite:///{tmp_path / 'm.db'}")()
    team, repo = make_team_repo(session)
    repo.get_or_create_member("same-id", "DT", channel="dingtalk")
    repo.get_or_create_member("same-id", "Web", channel="web")
    repo.commit()
    picked = get_team_member(session, team.id, "same-id")
    assert picked is not None
    assert picked.channel == "web"
    session.close()


def test_feishu_dispatch_reuses_feishu_member(tmp_path):
    session_factory = init_db(f"sqlite:///{tmp_path / 'f.db'}")
    session = session_factory()
    _team, repo = make_team_repo(session)
    existing = repo.get_or_create_member("ou_1", "Feishu User", channel="feishu")
    repo.commit()
    member_id = existing.id
    session.close()

    config = AppConfig(bot=BotPlatformConfig(name="feishu"))
    reply = dispatch_text_command(
        config=config,
        session_factory=session_factory,
        messenger=NullMessenger(),
        inbound=InboundMessage(
            channel="feishu",
            channel_user_id="ou_1",
            display_name="Feishu User",
            text="帮助",
            conversation_type="oto",
        ),
    )
    assert reply is not None

    session = session_factory()
    _team, repo = make_team_repo(session)
    assert repo.get_member_by_channel_user_id("ou_1", channel="feishu").id == member_id
    assert repo.get_member_by_channel_user_id("ou_1", channel="dingtalk") is None
    session.close()


def test_channel_member_respects_channel(tmp_path):
    session = init_db(f"sqlite:///{tmp_path / 'c.db'}")()
    _team, repo = make_team_repo(session)
    m = channel_member(repo, "u1", "Alice", channel="feishu")
    assert m.channel == "feishu"
    session.close()


def test_send_ok_false_for_null_messenger_skip():
    assert _send_ok({"ok": True, "skipped": True}) is False
    assert _send_ok(None) is True
    assert _send_ok({"ok": True}) is True
