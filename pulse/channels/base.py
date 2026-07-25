from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ChannelMessenger(Protocol):
    """渠道消息网关抽象（none / dingtalk / feishu / wecom）。"""

    def send_group_text(self, text: str, *, at_all: bool = False) -> dict: ...

    def send_oto_text(self, user_id: str, content: str) -> dict: ...

    def download_message_file(self, download_code: str, dest: Path) -> Path: ...


class ChannelRuntime(Protocol):
    """入站运行时：阻塞直到进程退出。"""

    def start(self, config, session_factory, messenger: ChannelMessenger) -> None: ...


class NullMessenger:
    """无 IM 时的出站实现：只记日志，不发送。"""

    def send_group_text(self, text: str, *, at_all: bool = False) -> dict:
        logger.info("NullMessenger: skip group text (at_all=%s) len=%s", at_all, len(text or ""))
        return {"ok": True, "skipped": True}

    def send_oto_text(self, user_id: str, content: str) -> dict:
        logger.info("NullMessenger: skip OTO to %s len=%s", user_id, len(content or ""))
        return {"ok": True, "skipped": True}

    def download_message_file(self, download_code: str, dest: Path) -> Path:
        raise RuntimeError("NullMessenger cannot download message files (no IM channel)")


class NullRuntime:
    """无 IM 入站：阻塞保活，仅配合 scheduler 使用。"""

    def start(self, config, session_factory, messenger: ChannelMessenger) -> None:
        import time

        logger.info("NullRuntime: no inbound channel (BOT_PLATFORM=none); scheduler-only mode")
        while True:
            time.sleep(3600)


def normalize_platform(name: str | None) -> str:
    platform = (name or "none").strip().lower()
    if platform in ("", "null", "off", "disabled"):
        return "none"
    return platform


def messenger_delivered(result: dict | None) -> bool:
    """True when an outbound send actually delivered (not NullMessenger skip)."""
    if result is None:
        return True
    if not isinstance(result, dict):
        return True
    if result.get("skipped"):
        return False
    if result.get("ok") is False:
        return False
    return True


def outbound_messenger_or_none(config: Any) -> ChannelMessenger | None:
    """Factory for publish/reply paths: None when platform is none or NullMessenger."""
    if normalize_platform(getattr(getattr(config, "bot", None), "name", None)) == "none":
        return None
    try:
        messenger = create_messenger(config)
    except Exception:
        logger.exception("create_messenger failed")
        return None
    if isinstance(messenger, NullMessenger):
        return None
    return messenger


def create_messenger(config: Any) -> ChannelMessenger:
    platform = normalize_platform(getattr(getattr(config, "bot", None), "name", None))
    if platform == "none":
        return NullMessenger()
    if platform == "dingtalk":
        from pulse.channels.dingtalk.messenger import DingTalkMessenger

        return DingTalkMessenger(config)
    if platform == "feishu":
        from pulse.channels.feishu.messenger import FeishuMessenger

        return FeishuMessenger(config)
    if platform in ("wecom", "wechat"):
        from pulse.channels.platforms.wecom import WeComMessenger

        return WeComMessenger(config)
    raise ValueError(f"未知 bot 平台：{platform}")


def create_runtime(config: Any) -> ChannelRuntime:
    platform = normalize_platform(getattr(getattr(config, "bot", None), "name", None))
    if platform == "none":
        return NullRuntime()
    if platform == "dingtalk":
        from pulse.channels.dingtalk.runtime import DingTalkRuntime

        return DingTalkRuntime()
    if platform == "feishu":
        from pulse.channels.feishu.runtime import FeishuRuntime

        return FeishuRuntime()
    if platform in ("wecom", "wechat"):
        raise RuntimeError(f"平台 {platform} 的运行时尚未实现")
    raise ValueError(f"未知 bot 平台：{platform}")
