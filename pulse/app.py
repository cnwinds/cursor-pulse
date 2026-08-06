from __future__ import annotations

import logging

from pulse.channels.base import create_messenger, create_runtime, messenger_delivered, normalize_platform
from pulse.channels.outbound_ledger import (
    record_outbound_ledger,
    resolve_group_conversation_id,
    resolve_outbound_channel,
    resolve_team_id,
)
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
            "group_open_conversation_id 未配置：群消息将无法发送。"
            "请在目标群内 @机器人 一次以自动绑定。"
        )

    session_factory = init_db(config.storage.database_url)
    messenger = create_messenger(config)
    runtime = create_runtime(config)
    cached_team_id: str | None = None

    def _ensure_team_id() -> str | None:
        nonlocal cached_team_id
        if cached_team_id:
            return cached_team_id
        try:
            boot = session_factory()
            try:
                cached_team_id = resolve_team_id(config, session=boot)
            finally:
                boot.close()
        except Exception:
            logger.exception("Failed to resolve team_id for outbound ledger")
        return cached_team_id

    def send_group_message(text: str, at_all: bool = False, *, source: str = "channel.group"):
        try:
            result = messenger.send_group_text(text, at_all=at_all)
        except Exception:
            logger.exception("Failed to send group message")
            return {"ok": False}
        if messenger_delivered(result):
            channel = resolve_outbound_channel(config)
            cid = resolve_group_conversation_id(config, channel) if channel else None
            tid = _ensure_team_id() if channel and cid else None
            if channel and cid and tid:
                record_outbound_ledger(
                    config,
                    team_id=tid,
                    channel=channel,
                    conversation_type="group",
                    conversation_id=cid,
                    text=text,
                    source=source,
                )
        return result

    def send_private_message(user_id: str, text: str, *, source: str = "channel.private"):
        try:
            result = messenger.send_oto_text(user_id, text)
        except Exception:
            logger.exception("Failed to send OTO message to %s", user_id)
            return {"ok": False}
        if messenger_delivered(result):
            channel = resolve_outbound_channel(config)
            tid = _ensure_team_id() if channel else None
            if channel and tid:
                record_outbound_ledger(
                    config,
                    team_id=tid,
                    channel=channel,
                    conversation_type="private",
                    user_id=user_id,
                    text=text,
                    source=source,
                )
        return result

    scheduler = build_scheduler(
        config, session_factory, send_group_message, send_private_message, messenger=messenger
    )
    scheduler.start()
    logger.info("Sync scheduler started (cursor sync + key loan expiry)")

    try:
        runtime.start(config, session_factory, messenger)
    finally:
        scheduler.shutdown()
