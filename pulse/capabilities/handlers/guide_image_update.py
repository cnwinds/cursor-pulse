from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.authz.actor import can_manage_guide_image
from pulse.channels.dingtalk.guide_image import save_guide_image_override
from pulse.storage.models import Member


def _raw_files_dir(config: Any) -> str:
    storage = getattr(config, "storage", None)
    if storage is None:
        return "data/raw"
    return (getattr(storage, "raw_files_dir", None) or "data/raw").strip() or "data/raw"


def _is_valid_image_bytes(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


def _decode_base64_image(value: str) -> bytes | None:
    payload = value.strip()
    if payload.startswith("data:"):
        comma = payload.find(",")
        if comma < 0:
            return None
        payload = payload[comma + 1 :]
    if not payload:
        return None
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError:
        return None


def _load_image_bytes(arguments: dict[str, Any]) -> bytes | None:
    image_base64 = arguments.get("image_base64")
    if isinstance(image_base64, str) and image_base64.strip():
        return _decode_base64_image(image_base64)

    image_path = arguments.get("image_path")
    if isinstance(image_path, str) and image_path.strip():
        path = Path(image_path.strip())
        try:
            if not path.is_file():
                return None
            return path.read_bytes()
        except OSError:
            return None

    return None


def handle_guide_image_update(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    if not request.confirmed_by:
        return CapabilityInvokeResult(
            status="failed",
            error_code="confirmation_required",
            user_message="该操作需要确认后执行",
        )

    member = session.get(Member, request.actor_member_id)
    if member is None or member.team_id != request.team_id:
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="成员不存在或无权访问",
        )

    if not can_manage_guide_image(member, config):
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="仅 owner、operator 或钉钉管理员可更新引导图",
        )

    image_bytes = _load_image_bytes(request.arguments)
    if image_bytes is None or not _is_valid_image_bytes(image_bytes):
        return CapabilityInvokeResult(
            status="failed",
            error_code="invalid_arguments",
            user_message="请提供有效的 image_base64 或 image_path 图片参数",
        )

    raw_files_dir = _raw_files_dir(config)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = Path(tmp.name)
        saved = save_guide_image_override(raw_files_dir, tmp_path)
    except OSError as exc:
        return CapabilityInvokeResult(
            status="failed",
            error_code="save_failed",
            user_message=f"引导图保存失败：{exc}",
        )
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "image_path": str(saved),
            "updated": True,
        },
    )
