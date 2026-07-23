from __future__ import annotations

import json
import re
from dataclasses import dataclass

VENDOR_VISION_JSON_SCHEMA = """
{
  "confidence": 0.0,
  "warnings": ["string"],
  "period_hint": "YYYY-MM or null",
  "primary_metric_value": 0.0,
  "primary_metric_unit": "calls|prompts|messages|usd|cny|tokens",
  "breakdown_by_model": {"GLM": 0.0}
}
"""

_VENDOR_PROMPTS: dict[str, str] = {
    "zhipu": """你是智谱 GLM Coding / 开放平台用量页截图提取助手。
从截图读取本月主用量指标（优先 MCP 调用次数或 prompts 次数；按量付费则读取 CNY 消费金额）。
输出严格 JSON，不要 markdown。看不清的字段写入 warnings。""",
    "minimax": """你是 MiniMax 控制台用量页截图提取助手。
从截图读取本月主用量（优先 API calls 次数；按量则读取 USD 消费）。
输出严格 JSON，不要 markdown。看不清的字段写入 warnings。""",
    "codex": """你是 ChatGPT / Codex 用量页截图提取助手。
从截图读取 5 小时滚动窗口内的 messages 用量，或当月 API 消费（USD）。
输出严格 JSON，不要 markdown。看不清的字段写入 warnings。""",
}


@dataclass(frozen=True)
class VendorVisionResult:
    primary_metric_value: float
    primary_metric_unit: str
    breakdown_by_model: dict[str, float]
    period_hint: str | None
    confidence: float
    warnings: list[str]


def vendor_vision_system_prompt(vendor_slug: str) -> str:
    intro = _VENDOR_PROMPTS.get(
        vendor_slug,
        "你是 AI 工具用量页截图提取助手。读取截图中的主用量数字。",
    )
    return (
        f"{intro}\n\n"
        "字段说明见 schema；不要编造看不清的数字；"
        "若完全无法识别，confidence 设为 0。\n\n"
        f"JSON schema:\n{VENDOR_VISION_JSON_SCHEMA}"
    )


def vendor_vision_user_prompt(vendor_slug: str) -> str:
    labels = {
        "zhipu": "智谱",
        "minimax": "MiniMax",
        "codex": "Codex/ChatGPT",
    }
    name = labels.get(vendor_slug, vendor_slug)
    return f"请提取这张{name}用量截图中的主指标。只输出 JSON，不要其他文字。"


def parse_vendor_vision_response(raw: str) -> VendorVisionResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    payload = json.loads(text)
    value = float(payload.get("primary_metric_value", 0))
    unit = str(payload.get("primary_metric_unit") or "calls").lower()
    breakdown = payload.get("breakdown_by_model") or {}
    if not isinstance(breakdown, dict):
        breakdown = {}
    breakdown = {str(k): float(v) for k, v in breakdown.items()}
    confidence = float(payload.get("confidence", 0))
    warnings = [str(w) for w in (payload.get("warnings") or [])]
    period_hint = payload.get("period_hint")
    if period_hint is not None:
        period_hint = str(period_hint)
    if value <= 0:
        raise ValueError("Vision 响应未包含有效主指标")
    return VendorVisionResult(
        primary_metric_value=value,
        primary_metric_unit=unit,
        breakdown_by_model=breakdown,
        period_hint=period_hint,
        confidence=max(0.0, min(1.0, confidence)),
        warnings=warnings,
    )
