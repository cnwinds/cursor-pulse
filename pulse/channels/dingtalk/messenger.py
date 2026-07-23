from __future__ import annotations

import json
import logging
import platform
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

from pulse.config import AppConfig

logger = logging.getLogger(__name__)

DINGTALK_OPENAPI = "https://api.dingtalk.com"
DINGTALK_OAPI = "https://oapi.dingtalk.com"
USER_AGENT = (
    f"CursorPulse/0.1 Python/{platform.python_version()} "
    "(+https://github.com/cursor-pulse)"
)
_MARKDOWN_TABLE_RE = re.compile(r"^\|.+\|\s*$", re.MULTILINE)
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,3}\s+\S", re.MULTILINE)
_MARKDOWN_BLOCKQUOTE_RE = re.compile(r"^>\s+", re.MULTILINE)
_MARKDOWN_BOLD_LIST_RE = re.compile(r"^-\s+\*\*", re.MULTILINE)
_MARKDOWN_HRULE_RE = re.compile(r"^---+\s*$", re.MULTILINE)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*.+?\*\*")
_MARKDOWN_BULLET_RE = re.compile(r"^-\s+\S", re.MULTILINE)


def _looks_like_markdown_message(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if _MARKDOWN_TABLE_RE.search(text):
        return True
    if _MARKDOWN_HEADING_RE.search(text):
        return True
    if _MARKDOWN_BLOCKQUOTE_RE.search(text):
        return True
    if _MARKDOWN_BOLD_LIST_RE.search(text):
        return True
    if _MARKDOWN_HRULE_RE.search(text):
        return True
    # LLM 改写后的用量/分析回复常见「列表 + 行内加粗」，不一定带 ### 或 - **
    if _MARKDOWN_BOLD_RE.search(text) and _MARKDOWN_BULLET_RE.search(text):
        return True
    if len(_MARKDOWN_BOLD_RE.findall(text)) >= 2:
        return True
    return False


def _markdown_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for prefix in ("### ", "## ", "# "):
            if stripped.startswith(prefix):
                return stripped[len(prefix) :].strip()[:64]
        if stripped.startswith("- **") and "**" in stripped[4:]:
            end = stripped.index("**", 4)
            return stripped[4:end].strip()[:64]
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            return stripped[2:-2].strip()[:64]
        return stripped[:64]
    return "小脉回复"


class DingTalkMessenger:
    """钉钉 OpenAPI：文件下载、OTO 单聊、主动群消息。"""

    def __init__(self, config: AppConfig, token_provider: Callable[[], str | None] | None = None):
        self.config = config
        self._token_provider = token_provider
        self._token_cache: dict[str, Any] = {}
        self._image_media_cache: dict[str, str] = {}

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

    def upload_image_media_id(self, image_path: Path) -> str:
        image_path = image_path.resolve()
        cache_key = f"{image_path}:{image_path.stat().st_mtime_ns}"
        cached = self._image_media_cache.get(cache_key)
        if cached:
            return cached

        token = self.get_access_token()
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        with image_path.open("rb") as fp:
            response = requests.post(
                f"{DINGTALK_OAPI}/media/upload",
                params={"access_token": token, "type": "image"},
                files={"media": (image_path.name, fp, mime)},
                timeout=60,
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errcode") not in (0, None):
            raise RuntimeError(payload.get("errmsg") or "钉钉图片上传失败")
        media_id = str(payload["media_id"])
        self._image_media_cache[cache_key] = media_id
        return media_id

    def clear_image_media_cache(self) -> None:
        self._image_media_cache.clear()

    def image_photo_url(self, image_path: Path) -> str:
        """兼容旧调用：返回 media_id（sampleImageMsg 的 photoURL 字段应填 media_id）。"""
        return self.upload_image_media_id(image_path)

    def reply_session_image(
        self,
        session_webhook: str,
        media_id: str,
        *,
        at_user_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "msgtype": "image",
            "image": {"media_id": media_id},
        }
        if at_user_id:
            payload["at"] = {"atUserIds": [at_user_id]}
        response = requests.post(
            session_webhook,
            headers={"Content-Type": "application/json", "Accept": "*/*"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()

    def reply_session_text(
        self,
        session_webhook: str,
        content: str,
        *,
        at_user_id: str | None = None,
    ) -> None:
        if _looks_like_markdown_message(content):
            payload: dict[str, Any] = {
                "msgtype": "markdown",
                "markdown": {
                    "title": _markdown_title(content),
                    "text": content,
                },
            }
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": content},
            }
        if at_user_id:
            payload["at"] = {"atUserIds": [at_user_id]}
        response = requests.post(
            session_webhook,
            headers={"Content-Type": "application/json", "Accept": "*/*"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()

    def send_oto_image(self, user_id: str, media_id: str) -> dict:
        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/robot/oToMessages/batchSend",
            headers=self._headers(),
            json={
                "robotCode": self.robot_code,
                "userIds": [user_id],
                "msgKey": "sampleImageMsg",
                "msgParam": json.dumps({"photoURL": media_id}, ensure_ascii=False),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def send_oto_image_file(
        self,
        user_id: str,
        image_path: Path,
        *,
        file_name: str | None = None,
    ) -> dict:
        media_id = self.upload_image_media_id(image_path)
        try:
            return self.send_oto_image(user_id, media_id)
        except Exception:
            logger.warning("sampleImageMsg failed, fallback to sampleFile", exc_info=True)
            name = file_name or image_path.name
            file_type = image_path.suffix.lower().lstrip(".") or "png"
            if file_type == "jpeg":
                file_type = "jpg"
            response = requests.post(
                f"{DINGTALK_OPENAPI}/v1.0/robot/oToMessages/batchSend",
                headers=self._headers(),
                json={
                    "robotCode": self.robot_code,
                    "userIds": [user_id],
                    "msgKey": "sampleFile",
                    "msgParam": json.dumps(
                        {
                            "mediaId": media_id,
                            "fileName": name,
                            "fileType": file_type,
                        },
                        ensure_ascii=False,
                    ),
                },
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

    def send_oto_text(self, user_id: str, content: str) -> dict:
        if _looks_like_markdown_message(content):
            return self._send_oto_markdown([user_id], content)
        return self._send_oto([user_id], content)

    def send_oto_text_batch(self, user_ids: list[str], content: str) -> dict:
        if _looks_like_markdown_message(content):
            return self._send_oto_markdown(user_ids, content)
        return self._send_oto(user_ids, content)

    def _send_oto_markdown(self, user_ids: list[str], content: str) -> dict:
        if not user_ids:
            raise ValueError("user_ids 不能为空")
        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/robot/oToMessages/batchSend",
            headers=self._headers(),
            json={
                "robotCode": self.robot_code,
                "userIds": user_ids[:20],
                "msgKey": "sampleMarkdown",
                "msgParam": json.dumps(
                    {
                        "title": _markdown_title(content),
                        "text": content,
                    },
                    ensure_ascii=False,
                ),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

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

        msg_key = "sampleMarkdown" if _looks_like_markdown_message(content) else "sampleText"
        msg_param = (
            {"title": _markdown_title(content), "text": content}
            if msg_key == "sampleMarkdown"
            else {"content": content}
        )

        response = requests.post(
            f"{DINGTALK_OPENAPI}/v1.0/robot/groupMessages/send",
            headers=self._headers(),
            json={
                "robotCode": self.robot_code,
                "openConversationId": open_conversation_id,
                "msgKey": msg_key,
                "msgParam": json.dumps(msg_param, ensure_ascii=False),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
