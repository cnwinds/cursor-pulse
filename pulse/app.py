from __future__ import annotations

import logging

from pulse.channels.base import create_messenger, create_runtime, normalize_platform
from pulse.channels.reminders.scheduler import build_scheduler
from pulse.config import AppConfig
from pulse.storage.db import init_db

logger = logging.getLogger(__name__)


def run_app(config: AppConfig) -> None:
    platform = normalize_platform(config.bot.name)
    if not config.admin.channel_user_ids and platform != "none":
        logger.error(
            "admin.channel_user_ids 未配置：渠道侧无人拥有管理员权限。"
            "请在 config.yaml 或 ADMIN_CHANNEL_USER_IDS 中设置至少一个管理员。"
        )
    if platform == "dingtalk" and not config.dingtalk.group_open_conversation_id:
        logger.warning(
            "group_open_conversation_id 未配置：群消息与月报将无法发送。"
            "请在目标群内 @机器人 一次以自动绑定。"
        )

    session_factory = init_db(config.storage.database_url)
    messenger = create_messenger(config)
    runtime = create_runtime(config)

    def send_group_message(text: str, at_all: bool = False):
        try:
            return messenger.send_group_text(text, at_all=at_all)
        except Exception:
            logger.exception("Failed to send group message")
            return {"ok": False}

    def send_private_message(user_id: str, text: str):
        try:
            return messenger.send_oto_text(user_id, text)
        except Exception:
            logger.exception("Failed to send OTO message to %s", user_id)
            return {"ok": False}

    scheduler = build_scheduler(
        config, session_factory, send_group_message, send_private_message, messenger=messenger
    )
    scheduler.start()
    if config.collection.reminders_enabled:
        logger.info("Reminder scheduler started (usage submission reminders enabled)")
    else:
        logger.info("Reminder scheduler started (usage submission reminders disabled)")

    try:
        runtime.start(config, session_factory, messenger)
    finally:
        scheduler.shutdown()
