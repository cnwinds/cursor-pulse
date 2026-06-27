from __future__ import annotations

import json
import logging
import platform
import time
from pathlib import Path
from typing import Any, Callable

import requests

from pulse.config import AppConfig

logger = logging.getLogger(__name__)

DINGTALK_OPENAPI = "https://api.dingtalk.com"
USER_AGENT = (
    f"CursorPulse/0.1 Python/{platform.python_version()} "
    "(+https://github.com/cursor-pulse)"
)


class DingTalkMessenger:
    """钉钉 OpenAPI：文件下载、OTO 单聊、主动群消息。"""

    def __init__(self, config: AppConfig, token_provider: Callable[[], str | None] | None = None):
        self.config = config
        self._token_provider = token_provider
        self._token_cache: dict[str, Any] = {}

    @property
    def robot_code(self) -> str:
        return self.config.dingtalk.robot_code or self.config.dingtalk.app_key

    def get_access_token(self) -> str:
        if self._token_provider:
            token = self._token_provider()
            if not token:
                raise RuntimeError("无法获取钉钉 access token")
            return token

        now = int(time.time())
        if self._token_cache and now < self._token_cache.get("expireTime", 0):
            return self._token_cache["accessToken"]

        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/oauth2/accessToken",
            headers={"Content-Type": "application/json"},
            json={
                "appKey": self.config.dingtalk.app_key,
                "appSecret": self.config.dingtalk.app_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        payload["expireTime"] = now + payload["expireIn"] - 300
        self._token_cache = payload
        return payload["accessToken"]

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-acs-dingtalk-access-token": self.get_access_token(),
            "User-Agent": USER_AGENT,
        }

    def get_download_url(self, download_code: str) -> str:
        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/robot/messageFiles/download",
            headers=self._headers(),
            json={"robotCode": self.robot_code, "downloadCode": download_code},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["downloadUrl"]

    def download_message_file(self, download_code: str, dest: Path) -> Path:
        url = self.get_download_url(download_code)
        file_response = requests.get(url, timeout=120)
        file_response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(file_response.content)
        return dest

    def send_oto_text(self, user_id: str, content: str) -> dict:
        return self._send_oto([user_id], content)

    def send_oto_text_batch(self, user_ids: list[str], content: str) -> dict:
        return self._send_oto(user_ids, content)

    def _send_oto(self, user_ids: list[str], content: str) -> dict:
        if not user_ids:
            raise ValueError("user_ids 不能为空")
        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/robot/oToMessages/batchSend",
            headers=self._headers(),
            json={
                "robotCode": self.robot_code,
                "userIds": user_ids[:20],
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": content}, ensure_ascii=False),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def resolve_open_conversation_id(self, chat_id: str) -> str:
        """chatId → openConversationId（需应用开通 qyapi_chat_read 权限）。"""
        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/im/chat/{chat_id}/convertToOpenConversationId",
            headers=self._headers(),
            json={},
            timeout=30,
        )
        if response.status_code == 403:
            raise RuntimeError(
                "缺少权限 qyapi_chat_read，无法通过群号转换。"
                "请在开放平台为应用开通「钉钉群基础信息读」权限，"
                "或在目标群内 @机器人 一次以自动捕获 openConversationId。"
            )
        response.raise_for_status()
        return response.json()["openConversationId"]

    def send_group_text(self, content: str, *, at_all: bool = False) -> dict:
        open_conversation_id = self.config.dingtalk.group_open_conversation_id
        if not open_conversation_id:
            raise RuntimeError("DINGTALK_GROUP_ID / group_open_conversation_id 未配置")

        if at_all:
            content = f"【@所有人】\n{content}"

        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/robot/groupMessages/send",
            headers=self._headers(),
            json={
                "robotCode": self.robot_code,
                "openConversationId": open_conversation_id,
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": content}, ensure_ascii=False),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
