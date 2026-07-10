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


def extract_picture_download_code(raw: dict) -> str | None:
    """从 Stream 回调提取图片 downloadCode。"""
    if incoming_message_type(raw) != "picture":
        return None
    content = _coerce_dict(raw.get("content"))
    return content.get("downloadCode") or content.get("download_code")


def inbox_dest(raw_files_dir: Path, file_name: str) -> Path:
    safe_name = Path(file_name).name
    return raw_files_dir / "inbox" / f"{uuid.uuid4().hex}_{safe_name}"
