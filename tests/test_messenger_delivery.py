from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pulse.channels.base import (
    NullMessenger,
    messenger_delivered,
    outbound_messenger_or_none,
)
from pulse.config import AppConfig, BotPlatformConfig, DingTalkConfig


def test_messenger_delivered_false_when_skipped():
    assert messenger_delivered({"ok": True, "skipped": True}) is False
    assert messenger_delivered({"ok": True}) is True
    assert messenger_delivered(None) is True


def test_outbound_messenger_or_none_for_platform_none():
    config = AppConfig(bot=BotPlatformConfig(name="none"))
    assert outbound_messenger_or_none(config) is None
    assert isinstance(NullMessenger(), NullMessenger)


def test_outbound_messenger_or_none_for_dingtalk():
    from pulse.channels.dingtalk.messenger import DingTalkMessenger

    config = AppConfig(
        bot=BotPlatformConfig(name="dingtalk"),
        dingtalk=DingTalkConfig(app_key="k", app_secret="s"),
    )
    messenger = outbound_messenger_or_none(config)
    assert isinstance(messenger, DingTalkMessenger)


def test_publish_report_raises_when_messenger_skips(tmp_path):
    from pathlib import Path

    from pulse.aggregate.engine import aggregate_period
    from pulse.config import CollectionConfig, TenantConfig
    from pulse.extract.csv_parser import parse_usage_events_csv
    from pulse.report.service import publish_report_to_group
    from pulse.storage.db import init_db
    from tests.conftest import make_team_repo

    sample = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"
    session = init_db(f"sqlite:///{tmp_path / 'r.db'}")()
    team, repo = make_team_repo(session)
    member = repo.add_member("user-1", "Alice")
    repo.save_csv_ingestion(
        member=member,
        period="2026-07",
        parsed=parse_usage_events_csv(sample),
        submit_channel="private",
    )
    repo.commit()
    aggregate_period(session, "2026-07", team_id=team.id)
    session.commit()

    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        collection=CollectionConfig(publish_report_to_group=True),
        bot=BotPlatformConfig(name="none"),
    )
    with pytest.raises(RuntimeError, match="messenger 不可用|未实际投递"):
        publish_report_to_group(
            session,
            "2026-07",
            outbound_messenger_or_none(config),
            team_id=team.id,
            reaggregate=False,
            config=config,
        )
    session.close()


def test_feishu_channel_reply_private(monkeypatch):
    from pulse.config import AppConfig, FeishuConfig
    from pulse.web.internal_channel_api import deliver_channel_reply

    messenger = MagicMock()
    messenger.send_oto_text.return_value = {"ok": True}
    config = AppConfig(
        bot=BotPlatformConfig(name="feishu"),
        feishu=FeishuConfig(app_id="a", app_secret="b"),
    )
    result = deliver_channel_reply(
        config,
        reply_endpoint={
            "channel": "feishu",
            "conversation_type": "private",
            "user_id": "ou_1",
        },
        text="hi",
        messenger=messenger,
    )
    assert result["status"] == "sent"
    messenger.send_oto_text.assert_called_once_with("ou_1", "hi")
