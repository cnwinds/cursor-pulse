from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ChannelMessenger(Protocol):
    """渠道消息网关抽象（钉钉 / 飞书 / 企微实现）。"""

    def send_group_text(self, text: str, *, at_all: bool = False) -> dict: ...

    def send_oto_text(self, user_id: str, content: str) -> dict: ...

    def download_message_file(self, download_code: str, dest: Path) -> Path: ...


def create_messenger(config):
    platform = (config.bot.name or "dingtalk").lower()
    if platform == "dingtalk":
        from pulse.channels.dingtalk.messenger import DingTalkMessenger

        return DingTalkMessenger(config)
    if platform == "feishu":
        from pulse.channels.platforms.feishu import FeishuMessenger

        return FeishuMessenger(config)
    if platform in ("wecom", "wechat"):
        from pulse.channels.platforms.wecom import WeComMessenger

        return WeComMessenger(config)
    raise ValueError(f"未知 bot 平台：{platform}")
