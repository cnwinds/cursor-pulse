from __future__ import annotations

from pulse.config import AppConfig


class WeComMessenger:
    """企业微信 Bot 扩展桩 — 需实现应用消息接入。"""

    def __init__(self, config: AppConfig):
        self.config = config

    def _not_impl(self, *args, **kwargs):
        raise NotImplementedError(
            "企业微信平台尚未实现。请使用 bot.name=dingtalk。"
        )

    send_group_text = _not_impl
    send_oto_text = _not_impl
    download_message_file = _not_impl
