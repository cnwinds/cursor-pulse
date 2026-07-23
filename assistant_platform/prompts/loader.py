from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_PROMPTS_ROOT = Path(__file__).resolve().parent

PERSONA_SUPPLEMENT_HEADER = (
    "## Prompt Studio（人设与语气补充）\n"
    "以下内容仅调整人设与表达风格；"
    "工具调用、权限、交互节奏与业务流程以系统前文规则为准。"
)


def _manifest_path() -> Path:
    return _PROMPTS_ROOT / "manifest.yaml"


def load_manifest() -> list[dict[str, Any]]:
    raw = yaml.safe_load(_manifest_path().read_text(encoding="utf-8")) or {}
    return list(raw.get("fragments") or [])


def load_prompt_fragments_from_files(*, root: Path | None = None) -> dict[str, str]:
    base = root or _PROMPTS_ROOT
    items = load_manifest() if root is None else (
        yaml.safe_load((base / "manifest.yaml").read_text(encoding="utf-8")) or {}
    ).get("fragments") or []
    out: dict[str, str] = {}
    for item in items:
        key = str(item["key"]).strip()
        rel = str(item["path"]).strip()
        path = base / rel
        if not path.is_file():
            raise FileNotFoundError(f"prompt fragment missing: {path}")
        out[key] = path.read_text(encoding="utf-8").strip()
    return out


def compose_system_supplement_from_files(*, root: Path | None = None) -> str:
    fragments = load_prompt_fragments_from_files(root=root)
    parts: list[str] = []
    for key in ("heart.md", "precepts.md"):
        content = fragments.get(key, "").strip()
        if content:
            parts.append(f"## {key}\n{content}")
    if not parts:
        return ""
    return PERSONA_SUPPLEMENT_HEADER + "\n\n" + "\n\n".join(parts)
