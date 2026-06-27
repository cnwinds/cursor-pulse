from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PATH = Path("data/dingtalk_group.json")


def load_persisted_group_id(path: Path = DEFAULT_PATH) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("open_conversation_id") or None
    except (json.JSONDecodeError, OSError):
        return None


def save_group_binding(
    *,
    open_conversation_id: str,
    chat_id: str | None = None,
    title: str | None = None,
    path: Path = DEFAULT_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "open_conversation_id": open_conversation_id,
        "chat_id": chat_id,
        "title": title,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
