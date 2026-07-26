from __future__ import annotations

import logging

from pulse.channels.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig

logger = logging.getLogger(__name__)


def start_dingtalk_bot(
    config: AppConfig,
    session_factory,
    messenger: DingTalkMessenger | None = None,
) -> None:
    if not config.dingtalk.app_key or not config.dingtalk.app_secret:
        raise RuntimeError("DINGTALK_APP_KEY and DINGTALK_APP_SECRET are required")

    try:
        import dingtalk_stream
    except ImportError as exc:
        raise RuntimeError(
            "钉钉运行时依赖 dingtalk-stream。"
            "请安装：pip install 'cursor-pulse[dingtalk]' 或 pip install dingtalk-stream"
        ) from exc

    from pulse.channels.dingtalk.handler import DingTalkChannelHandler

    credential = dingtalk_stream.Credential(config.dingtalk.app_key, config.dingtalk.app_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential, logger=logger)

    if messenger is None:
        messenger = DingTalkMessenger(config)
    messenger._token_provider = client.get_access_token  # noqa: SLF001 — 与 Stream 共用 token 缓存

    handler = DingTalkChannelHandler(config, session_factory, messenger, logger=logger)
    client.register_callback_handler(dingtalk_stream.chatbot.ChatbotMessage.TOPIC, handler)
    logger.info("Starting DingTalk Stream client...")
    client.start_forever()
