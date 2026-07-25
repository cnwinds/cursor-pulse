from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pulse.channels.dingtalk.mirror import (
    build_event_from_dingtalk,
    mirror_dingtalk_message,
    mirror_dingtalk_message_sync,
)
from pulse.config import AppConfig, AssistantMirrorConfig


def test_build_event_maps_ids():
    incoming = MagicMock()
    incoming.message_id = "msg-9"
    incoming.conversation_type = "1"
    incoming.conversation_id = "cid"
    incoming.sender_staff_id = "staff-1"
    incoming.sender_id = None
    incoming.sender_nick = "Bob"
    cfg = AppConfig()
    event = build_event_from_dingtalk(
        incoming,
        text="hello",
        config=cfg,
        team_id="team-xyz",
        is_group=False,
    )
    assert event.channel == "dingtalk"
    assert event.channel_message_id == "msg-9"
    assert event.sender_channel_user_id == "staff-1"
    assert event.conversation_type == "private"
    assert event.conversation_id == "staff-1"
    assert event.team_id == "team-xyz"


@pytest.mark.asyncio
async def test_mirror_posts_when_enabled_async():
    cfg = AppConfig(
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        )
    )
    incoming = MagicMock()
    incoming.message_id = "m1"
    incoming.conversation_type = "1"
    incoming.conversation_id = "c"
    incoming.sender_staff_id = "u"
    incoming.sender_id = "u"
    incoming.sender_nick = "N"
    with patch("pulse.channels.dingtalk.mirror.internal_async_client") as AsyncClient:
        client = AsyncClient.return_value.__aenter__.return_value
        client.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: {"created": True})
        )
        await mirror_dingtalk_message(
            incoming,
            text="hi",
            config=cfg,
            team_id="t1",
            is_group=False,
        )
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        assert args[0].endswith("/api/assistant/v1/events/messages")


@pytest.mark.asyncio
async def test_mirror_retries_with_async_sleep():
    cfg = AppConfig(
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        )
    )
    incoming = MagicMock()
    incoming.message_id = "m1"
    incoming.conversation_type = "1"
    incoming.conversation_id = "c"
    incoming.sender_staff_id = "u"
    incoming.sender_id = "u"
    incoming.sender_nick = "N"
    ok_resp = MagicMock(status_code=200)
    ok_resp.raise_for_status = MagicMock()
    with (
        patch("pulse.channels.dingtalk.mirror.internal_async_client") as AsyncClient,
        patch("pulse.channels.dingtalk.mirror.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        client = AsyncClient.return_value.__aenter__.return_value
        client.post = AsyncMock(side_effect=[httpx.ConnectError("down"), ok_resp])
        await mirror_dingtalk_message(
            incoming,
            text="hi",
            config=cfg,
            team_id="t1",
            is_group=False,
        )
        sleep.assert_awaited_once()
        assert client.post.await_count == 2


def test_mirror_posts_when_enabled_sync_wrapper():
    cfg = AppConfig(
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        )
    )
    incoming = MagicMock()
    incoming.message_id = "m1"
    incoming.conversation_type = "1"
    incoming.conversation_id = "c"
    incoming.sender_staff_id = "u"
    incoming.sender_id = "u"
    incoming.sender_nick = "N"
    with patch("pulse.channels.dingtalk.mirror.internal_client") as Client:
        client = Client.return_value.__enter__.return_value
        client.post.return_value = MagicMock(status_code=200, json=lambda: {"created": True})
        mirror_dingtalk_message_sync(
            incoming,
            text="hi",
            config=cfg,
            team_id="t1",
            is_group=False,
        )
        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args[0].endswith("/api/assistant/v1/events/messages")
