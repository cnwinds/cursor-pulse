from __future__ import annotations

from unittest.mock import MagicMock

from pulse.config import AppConfig, TenantConfig
from pulse.web.internal_channel_api import (
    _abort_delivery,
    _dedupe_key,
    _try_begin_delivery,
    deliver_channel_reply,
)


def test_dedupe_claim_blocks_second_begin():
    key = _dedupe_key(message_id="msg-1", text="hello", kind="interim")
    assert key == "msg-1:interim"
    assert _try_begin_delivery(key) is True
    assert _try_begin_delivery(key) is False


def test_failed_delivery_releases_dedupe_for_retry():
    config = AppConfig(tenant=TenantConfig(slug="t", name="T"))
    messenger = MagicMock()
    messenger.send_oto_text.side_effect = [
        RuntimeError("boom"),
        {"ok": True},
    ]
    endpoint = {
        "channel": "dingtalk",
        "conversation_type": "private",
        "user_id": "u1",
    }
    first = deliver_channel_reply(
        config,
        reply_endpoint=endpoint,
        text="hi",
        messenger=messenger,
        assistant_message_id="msg-retry-1",
        kind="final",
    )
    assert first["status"] == "queued"
    assert first["reason"] == "private_send_failed"

    second = deliver_channel_reply(
        config,
        reply_endpoint=endpoint,
        text="hi",
        messenger=messenger,
        assistant_message_id="msg-retry-1",
        kind="final",
    )
    assert second["status"] == "sent"
    assert messenger.send_oto_text.call_count == 2

    third = deliver_channel_reply(
        config,
        reply_endpoint=endpoint,
        text="hi",
        messenger=messenger,
        assistant_message_id="msg-retry-1",
        kind="final",
    )
    assert third == {"status": "sent", "reason": "deduplicated"}
    assert messenger.send_oto_text.call_count == 2


def test_abort_delivery_clears_claim():
    key = _dedupe_key(message_id="msg-abort", text="x", kind="final")
    assert _try_begin_delivery(key) is True
    _abort_delivery(key)
    assert _try_begin_delivery(key) is True
