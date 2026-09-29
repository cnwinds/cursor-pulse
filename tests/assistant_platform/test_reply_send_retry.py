"""reply.send must fail the job when channel delivery does not succeed."""

from __future__ import annotations

import pytest

from assistant_platform.config import AssistantConfig
from assistant_platform.jobs.worker import _handle_reply_send


def test_handle_reply_send_raises_when_delivery_failed(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.jobs.worker.send_channel_reply",
        lambda payload, config: {"status": "failed"},
    )
    with pytest.raises(RuntimeError, match="reply.send delivery failed"):
        _handle_reply_send({"text": "hi"}, AssistantConfig())


def test_handle_reply_send_raises_when_queued(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.jobs.worker.send_channel_reply",
        lambda payload, config: {"status": "queued", "reason": "messenger_unavailable"},
    )
    with pytest.raises(RuntimeError, match="status=queued"):
        _handle_reply_send({"text": "hi"}, AssistantConfig())


def test_handle_reply_send_ok_when_sent(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.jobs.worker.send_channel_reply",
        lambda payload, config: {"status": "sent"},
    )
    _handle_reply_send({"text": "hi"}, AssistantConfig())


def test_handle_reply_send_ok_when_skipped(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.jobs.worker.send_channel_reply",
        lambda payload, config: {"status": "skipped", "reason": "no_internal_token"},
    )
    _handle_reply_send({"text": "hi"}, AssistantConfig())
