from __future__ import annotations

import re
import uuid
from pathlib import Path

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


def extract_file_attachment(raw: dict) -> FileAttachment | None:
    """从 Stream 回调原始 JSON 提取文件（单聊 file 消息）。"""
    msgtype = raw.get("msgtype")
    if msgtype != "file":
        return None
    content = raw.get("content") or {}
    file_name = content.get("fileName") or content.get("file_name") or "upload.csv"
    download_code = content.get("downloadCode") or content.get("download_code")
    if not download_code:
        return None
    return file_name, download_code


def extract_picture_download_code(raw: dict) -> str | None:
    """从 Stream 回调提取图片 downloadCode。"""
    if raw.get("msgtype") != "picture":
        return None
    content = raw.get("content") or {}
    return content.get("downloadCode") or content.get("download_code")


def inbox_dest(raw_files_dir: Path, file_name: str) -> Path:
    safe_name = Path(file_name).name
    return raw_files_dir / "inbox" / f"{uuid.uuid4().hex}_{safe_name}"
