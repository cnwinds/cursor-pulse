from __future__ import annotations

from pulse.config import AppConfig


class FeishuMessenger:
    """飞书 Bot 扩展桩 — 需实现 Stream/Webhook 接入。"""

    def __init__(self, config: AppConfig):
        self.config = config

    def _not_impl(self, *args, **kwargs):
        raise NotImplementedError(
            "飞书平台尚未实现。请使用 bot.name=dingtalk。"
        )

    send_group_text = _not_impl
    send_oto_text = _not_impl
    download_message_file = _not_impl
