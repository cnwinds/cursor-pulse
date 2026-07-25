from __future__ import annotations

from pulse.channels.dingtalk.client import start_dingtalk_bot


class DingTalkRuntime:
    """钉钉 Stream 入站运行时。"""

    def start(self, config, session_factory, messenger) -> None:
        start_dingtalk_bot(config, session_factory, messenger=messenger)
