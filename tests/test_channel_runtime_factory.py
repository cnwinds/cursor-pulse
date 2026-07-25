from __future__ import annotations

import pytest

from pulse.channels.base import (
    NullMessenger,
    NullRuntime,
    create_messenger,
    create_runtime,
)
from pulse.config import AppConfig


def _config(platform: str) -> AppConfig:
    config = AppConfig()
    config.bot.name = platform
    return config


def test_create_messenger_and_runtime_for_none_platform():
    config = _config("none")
    messenger = create_messenger(config)
    runtime = create_runtime(config)
    assert isinstance(messenger, NullMessenger)
    assert isinstance(runtime, NullRuntime)


def test_create_messenger_and_runtime_for_dingtalk_platform():
    from pulse.channels.dingtalk.messenger import DingTalkMessenger
    from pulse.channels.dingtalk.runtime import DingTalkRuntime

    config = _config("dingtalk")
    messenger = create_messenger(config)
    runtime = create_runtime(config)
    assert isinstance(messenger, DingTalkMessenger)
    assert isinstance(runtime, DingTalkRuntime)


def test_create_messenger_and_runtime_for_feishu_platform():
    from pulse.channels.feishu.messenger import FeishuMessenger
    from pulse.channels.feishu.runtime import FeishuRuntime

    config = _config("feishu")
    config.feishu.app_id = "cli_test"
    config.feishu.app_secret = "secret"
    messenger = create_messenger(config)
    runtime = create_runtime(config)
    assert isinstance(messenger, FeishuMessenger)
    assert isinstance(runtime, FeishuRuntime)


def test_create_messenger_normalizes_off_aliases():
    for alias in ("null", "off", "disabled", ""):
        config = _config(alias)
        assert isinstance(create_messenger(config), NullMessenger)
        assert isinstance(create_runtime(config), NullRuntime)


def test_default_bot_platform_is_none():
    config = AppConfig()
    assert isinstance(create_messenger(config), NullMessenger)
    assert isinstance(create_runtime(config), NullRuntime)


def test_create_messenger_raises_for_unknown_platform():
    config = _config("unknown-platform")
    with pytest.raises(ValueError):
        create_messenger(config)
    with pytest.raises(ValueError):
        create_runtime(config)


def test_create_runtime_wecom_not_implemented():
    config = _config("wecom")
    with pytest.raises(RuntimeError):
        create_runtime(config)
