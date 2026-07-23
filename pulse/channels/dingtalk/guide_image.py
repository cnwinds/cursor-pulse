from __future__ import annotations

from pathlib import Path

BIND_KEY_GUIDE_MARKER = "绑定 cursor"

DEFAULT_GUIDE_IMAGE = (
    Path(__file__).resolve().parent.parent / "assets" / "cursor_bind_key_guide.png"
)


def override_guide_image_path(raw_files_dir: str | Path) -> Path:
    return Path(raw_files_dir) / "assets" / "cursor_bind_key_guide.png"


def resolve_guide_image_path(raw_files_dir: str | Path | None = None) -> Path | None:
    if raw_files_dir:
        override = override_guide_image_path(raw_files_dir)
        if override.is_file():
            return override
    if DEFAULT_GUIDE_IMAGE.is_file():
        return DEFAULT_GUIDE_IMAGE
    return None


def should_attach_bind_guide_image(text: str) -> bool:
    return BIND_KEY_GUIDE_MARKER in text and "未绑 Key" in text


def save_guide_image_override(raw_files_dir: str | Path, source: Path) -> Path:
    dest = override_guide_image_path(raw_files_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return dest
