"""FeishuMessenger — outbound messaging via the Feishu (Lark) Open API.

Uses ``httpx`` (already a project dependency) against the REST endpoints
documented at https://open.feishu.cn/document/server-docs/im-v1/message/create
Auth: tenant_access_token, obtained via app_id/app_secret and cached until
shortly before expiry (mirrors ``DingTalkMessenger.get_access_token``).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from pulse.config import AppConfig
from pulse.http_clients import outbound_client

logger = logging.getLogger(__name__)

FEISHU_OPEN_API = "https://open.feishu.cn/open-apis"


class FeishuMessenger:
    """飞书 Open API：tenant_access_token 缓存 + OTO/群文本发送。"""

    def __init__(self, config: AppConfig, http_client: httpx.Client | None = None):
        self.config = config
        self._client = http_client or outbound_client(timeout=30.0)
        self._owns_client = http_client is None
        self._token_cache: dict[str, Any] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_access_token(self) -> str:
        now = time.time()
        if self._token_cache and now < self._token_cache.get("expire_at", 0):
            return self._token_cache["token"]

        response = self._client.post(
            f"{FEISHU_OPEN_API}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.config.feishu.app_id,
                "app_secret": self.config.feishu.app_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"飞书获取 tenant_access_token 失败：{payload.get('msg')}")
        token = payload["tenant_access_token"]
        expire_in = int(payload.get("expire", 7200))
        self._token_cache = {"token": token, "expire_at": now + expire_in - 300}
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _send_text(self, receive_id: str, receive_id_type: str, content: str) -> dict:
        if not receive_id:
            raise ValueError("receive_id 不能为空")
        response = self._client.post(
            f"{FEISHU_OPEN_API}/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers=self._headers(),
            json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": content}, ensure_ascii=False),
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"飞书消息发送失败：{payload.get('msg')}")
        return payload

    def send_group_text(self, text: str, *, at_all: bool = False) -> dict:
        chat_id = self.config.feishu.group_chat_id
        if not chat_id:
            raise RuntimeError("FEISHU_GROUP_CHAT_ID / feishu.group_chat_id 未配置")
        content = f"<at user_id=\"all\">所有人</at> {text}" if at_all else text
        return self._send_text(chat_id, "chat_id", content)

    def send_oto_text(self, user_id: str, content: str) -> dict:
        return self._send_text(user_id, "open_id", content)

    def send_text_to_chat(self, chat_id: str, content: str) -> dict:
        """Reply into whichever chat an inbound event came from (group or OTO)."""
        return self._send_text(chat_id, "chat_id", content)

    def download_message_file(self, download_code: str, dest: Path) -> Path:
        """Download a message resource (image/file).

        Feishu keys resources by ``(message_id, file_key)`` rather than a
        single opaque download code; callers pass ``"{message_id}:{file_key}"``.
        """
        try:
            message_id, file_key = download_code.split(":", 1)
        except ValueError as exc:
            raise ValueError(
                "download_code 需为 'message_id:file_key' 形式（飞书资源下载）"
            ) from exc
        response = self._client.get(
            f"{FEISHU_OPEN_API}/im/v1/messages/{message_id}/resources/{file_key}",
            params={"type": "file"},
            headers={"Authorization": f"Bearer {self.get_access_token()}"},
        )
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest
