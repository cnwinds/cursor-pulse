"""FeishuRuntime — inbound runtime for the Feishu (Lark) channel.

Transport choice: WebSocket long connection via the official ``lark-oapi``
SDK (optional dependency; see the ``feishu`` extra in ``pyproject.toml``).
Like DingTalk Stream, this avoids needing a publicly reachable HTTPS
endpoint / webhook signature verification for self-hosted or small-team
deployments — no inbound firewall rule, no `encrypt_key`/`verification_token`
to manage, the SDK maintains the connection and retries on its own.

If ``lark-oapi`` isn't installed, ``start()`` raises a clear ``RuntimeError``
instead of silently falling back to a webhook server. A webhook transport
(``FEISHU_EVENT_PORT``) can be added later without changing the
``FeishuMessenger`` / ``dispatch_text_command`` contract, since all the
channel-specific parsing already lives in ``pulse.channels.feishu.handler``.
"""

from __future__ import annotations

import logging

from pulse.channels.feishu.handler import FeishuEventHandler
from pulse.channels.feishu.messenger import FeishuMessenger
from pulse.config import AppConfig

logger = logging.getLogger(__name__)


class FeishuRuntime:
    """飞书事件订阅（WebSocket 长连接）入站运行时。"""

    def start(
        self,
        config: AppConfig,
        session_factory,
        messenger: FeishuMessenger | None = None,
    ) -> None:
        if not config.feishu.app_id or not config.feishu.app_secret:
            raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

        try:
            import lark_oapi as lark
        except ImportError as exc:
            raise RuntimeError(
                "飞书运行时依赖 lark-oapi（WebSocket 长连接）。"
                "请安装：pip install 'cursor-pulse[feishu]' 或 pip install lark-oapi"
            ) from exc

        if messenger is None:
            messenger = FeishuMessenger(config)

        event_handler_impl = FeishuEventHandler(config, session_factory, messenger)

        dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(event_handler_impl.handle_message_receive)
            .build()
        )

        client = lark.ws.Client(
            config.feishu.app_id,
            config.feishu.app_secret,
            event_handler=dispatcher,
            log_level=lark.LogLevel.INFO,
        )
        logger.info("Starting Feishu WebSocket client...")
        client.start()
