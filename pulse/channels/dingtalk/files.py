from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

FileAttachment = tuple[str, str]  # (file_name, download_code)


def normalize_incoming_text(text: str) -> str:
    """去掉群消息里 @机器人 前缀。"""
    text = text.strip()
    if not text:
        return text
    lines = text.splitlines()
    first = lines[0].strip()
    if first.startswith("@"):
        first = re.sub(r"^@\S+\s*", "", first).strip()
        lines[0] = first
    return "\n".join(lines).strip()


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def incoming_message_type(raw: dict, incoming: Any | None = None) -> str | None:
    for candidate in (
        raw.get("msgtype"),
        raw.get("msgType"),
        getattr(incoming, "message_type", None) if incoming is not None else None,
    ):
        if candidate:
            return str(candidate).lower()
    return None


def _extract_from_content_blob(content: dict[str, Any]) -> FileAttachment | None:
    file_name = (
        content.get("fileName")
        or content.get("file_name")
        or content.get("name")
        or "upload.csv"
    )
    download_code = content.get("downloadCode") or content.get("download_code")
    if not download_code:
        return None
    return str(file_name), str(download_code)


def _extract_from_rich_text(raw: dict, incoming: Any | None = None) -> FileAttachment | None:
    content = _coerce_dict(raw.get("content"))
    if not content and incoming is not None:
        rich = getattr(incoming, "rich_text_content", None)
        if rich is not None and hasattr(rich, "to_dict"):
            content = _coerce_dict(rich.to_dict())
        if not content:
            content = _coerce_dict(getattr(incoming, "extensions", {}).get("content"))
    rich_list = content.get("richText") or content.get("rich_text") or []
    if not isinstance(rich_list, list):
        return None
    for item in rich_list:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").lower()
        if item_type and item_type not in {"file", "attachment"}:
            continue
        found = _extract_from_content_blob(item)
        if found:
            return found
    return None


def extract_file_attachment(raw: dict, incoming: Any | None = None) -> FileAttachment | None:
    """从 Stream 回调原始 JSON / ChatbotMessage 提取文件（单聊 file 消息）。"""
    msgtype = incoming_message_type(raw, incoming)

    if msgtype == "file":
        content = _coerce_dict(raw.get("content"))
        if not content and incoming is not None:
            content = _coerce_dict(getattr(incoming, "extensions", {}).get("content"))
        found = _extract_from_content_blob(content)
        if found:
            return found

    if msgtype in {"file", "richtext"}:
        found = _extract_from_rich_text(raw, incoming)
        if found:
            return found

    return None


def extract_incoming_text(incoming: Any | None) -> str:
    """从 text / richText 消息提取用户文本。"""
    if incoming is None:
        return ""
    if getattr(incoming, "text", None) and incoming.text.content:
        return normalize_incoming_text(incoming.text.content)
    if getattr(incoming, "message_type", None) == "richText" and hasattr(incoming, "get_text_list"):
        parts = [str(part).strip() for part in (incoming.get_text_list() or []) if str(part).strip()]
        if parts:
            return normalize_incoming_text("\n".join(parts))
    return ""


def _picture_code_from_rich_text(raw: dict, incoming: Any | None = None) -> str | None:
    if incoming is not None and hasattr(incoming, "get_image_list"):
        images = incoming.get_image_list() or []
        if images:
            return str(images[0])
    content = _coerce_dict(raw.get("content"))
    if not content and incoming is not None:
        rich = getattr(incoming, "rich_text_content", None)
        if rich is not None and hasattr(rich, "to_dict"):
            content = _coerce_dict(rich.to_dict())
    rich_list = content.get("richText") or content.get("rich_text") or []
    if not isinstance(rich_list, list):
        return None
    for item in rich_list:
        if not isinstance(item, dict):
            continue
        code = item.get("downloadCode") or item.get("download_code")
        if code:
            return str(code)
    return None


def extract_picture_download_code(raw: dict, incoming: Any | None = None) -> str | None:
    """从 Stream 回调 / ChatbotMessage 提取图片 downloadCode。"""
    msgtype = incoming_message_type(raw, incoming)
    if msgtype == "picture":
        content = _coerce_dict(raw.get("content"))
        code = content.get("downloadCode") or content.get("download_code")
        if code:
            return str(code)
        image = getattr(incoming, "image_content", None) if incoming is not None else None
        if image is not None and getattr(image, "download_code", None):
            return str(image.download_code)
    if msgtype in {"picture", "richtext", "richtext"} or (
        incoming is not None and getattr(incoming, "message_type", None) == "richText"
    ):
        return _picture_code_from_rich_text(raw, incoming)
    return None


def inbox_dest(raw_files_dir: Path, file_name: str) -> Path:
    safe_name = Path(file_name).name
    return raw_files_dir / "inbox" / f"{uuid.uuid4().hex}_{safe_name}"
