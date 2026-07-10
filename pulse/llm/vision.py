from __future__ import annotations

import logging
from pathlib import Path

from pulse.extract.vision_parser import VISION_JSON_SCHEMA, VisionExtractResult, parse_vision_response
from pulse.extract.vendor_vision import (
    VendorVisionResult,
    parse_vendor_vision_response,
    vendor_vision_system_prompt,
    vendor_vision_user_prompt,
)
from pulse.llm.client import LLMClient

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = f"""你是 Cursor Usage 页面截图的结构化提取助手。

任务：从截图 OCR 并提取 usage 事件行，输出严格 JSON（不要 markdown）。

字段说明见 schema；缺失 token 列可填 0；Cost 只能是 Included / Free / 数字 / -。
confidence 表示你对整体提取准确度的评估（0~1）。
warnings 列出看不清或缺失的字段说明。
若完全无法识别表格，confidence 设为 0 且 records 为空数组。

JSON schema:
{VISION_JSON_SCHEMA}
"""


def extract_usage_from_screenshot(image_path: Path, client: LLMClient, *, model: str) -> VisionExtractResult:
    user = (
        "请提取这张 Cursor Usage 截图中的所有可见事件行。"
        "只输出 JSON，不要其他文字。"
    )
    raw = client.complete_with_image(
        system=VISION_SYSTEM_PROMPT,
        user=user,
        image_path=image_path,
        model=model,
    )
    return parse_vision_response(raw)


def extract_vendor_usage_from_screenshot(
    image_path: Path,
    client: LLMClient,
    *,
    vendor_slug: str,
    model: str,
) -> VendorVisionResult:
    raw = client.complete_with_image(
        system=vendor_vision_system_prompt(vendor_slug),
        user=vendor_vision_user_prompt(vendor_slug),
        image_path=image_path,
        model=model,
    )
    return parse_vendor_vision_response(raw)
