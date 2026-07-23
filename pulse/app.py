from __future__ import annotations

import logging

from pulse.channels.base import create_messenger
from pulse.channels.dingtalk.client import start_dingtalk_bot
from pulse.channels.reminders.scheduler import build_scheduler
from pulse.config import AppConfig
from pulse.storage.db import init_db

logger = logging.getLogger(__name__)


def run_app(config: AppConfig) -> None:
    if not config.admin.dingtalk_user_ids:
        logger.error(
            "admin.dingtalk_user_ids 未配置：钉钉侧无人拥有管理员权限。"
            "请在 config.yaml 或 DINGTALK_ADMIN_USER_IDS 中设置至少一个管理员。"
        )
    if not config.dingtalk.group_open_conversation_id:
        logger.warning(
            "group_open_conversation_id 未配置：群消息与月报将无法发送。"
            "请在目标群内 @机器人 一次以自动绑定。"
        )

    session_factory = init_db(config.storage.database_url)
    messenger = create_messenger(config)
    platform = (config.bot.name or "dingtalk").lower()
    if platform != "dingtalk":
        logger.warning("当前平台 %s 为扩展桩，生产环境请使用 dingtalk", platform)

    def send_group_message(text: str, at_all: bool = False) -> None:
        try:
            messenger.send_group_text(text, at_all=at_all)
        except Exception:
            logger.exception("Failed to send group message")

    def send_private_message(user_id: str, text: str) -> None:
        try:
            messenger.send_oto_text(user_id, text)
        except Exception:
            logger.exception("Failed to send OTO message to %s", user_id)

    scheduler = build_scheduler(
        config, session_factory, send_group_message, send_private_message, messenger=messenger
    )
    scheduler.start()
    if config.collection.reminders_enabled:
        logger.info("Reminder scheduler started (usage submission reminders enabled)")
    else:
        logger.info("Reminder scheduler started (usage submission reminders disabled)")

    try:
        if platform == "dingtalk":
            start_dingtalk_bot(config, session_factory, messenger=messenger)
        else:
            raise RuntimeError(f"平台 {platform} 的运行时尚未实现，请设置 BOT_PLATFORM=dingtalk")
    finally:
        scheduler.shutdown()
