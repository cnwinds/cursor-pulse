from __future__ import annotations

import time

import pytest

from pulse.channels.base import NullMessenger, NullRuntime


def test_null_messenger_send_group_text_skips_and_returns_ok():
    messenger = NullMessenger()
    result = messenger.send_group_text("hello", at_all=True)
    assert result == {"ok": True, "skipped": True}


def test_null_messenger_send_oto_text_skips_and_returns_ok():
    messenger = NullMessenger()
    result = messenger.send_oto_text("user-1", "hi there")
    assert result == {"ok": True, "skipped": True}


def test_null_messenger_download_message_file_raises():
    messenger = NullMessenger()
    with pytest.raises(RuntimeError):
        messenger.download_message_file("code-1", "/tmp/does-not-matter")


def test_null_runtime_start_blocks_forever(monkeypatch):
    sleep_calls: list[float] = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise KeyboardInterrupt  # break out of the infinite loop for the test

    monkeypatch.setattr(time, "sleep", fake_sleep)

    runtime = NullRuntime()
    with pytest.raises(KeyboardInterrupt):
        runtime.start(config=None, session_factory=None, messenger=NullMessenger())

    assert sleep_calls == [3600]
