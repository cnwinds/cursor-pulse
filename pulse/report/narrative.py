from __future__ import annotations

import json
import logging

from pulse.llm.audit import find_unauthorized_numbers
from pulse.llm.client import LLMClient
from pulse.report.insights import generate_insights

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 Cursor Pulse 团队用量报告的洞察撰写助手。

铁律（不可违反）：
1. 你只能使用用户提供的 JSON 中已存在的数字、姓名、模型名。
2. 禁止估算、补充、四舍五入或修改任何数值。
3. 若 JSON 中无相关字段，明确说「数据中暂无此项」。
4. 洞察只做语言归纳与管理提醒，不可引入新数字。
5. 输出 3–5 条 bullet，每条以「· 」开头，不要重复 JSON 全文，不要编造原因。

输出格式：
【洞察】
· ...
"""

USER_PROMPT_TEMPLATE = """请基于以下已计算好的事实层 JSON，撰写洞察段落（仅归纳与提醒）：

{metrics_json}
"""


def generate_llm_insights(metrics: dict, client: LLMClient) -> str:
    user = USER_PROMPT_TEMPLATE.format(
        metrics_json=json.dumps(metrics, ensure_ascii=False, indent=2)
    )
    text = client.complete(system=SYSTEM_PROMPT, user=user)
    if not text.startswith("【洞察】"):
        text = "【洞察】\n" + text
    violations = find_unauthorized_numbers(text, metrics)
    if violations:
        raise ValueError(f"LLM 叙述含未授权数字: {', '.join(violations)}")
    return text


def generate_insights_with_fallback(metrics: dict, client: LLMClient | None) -> tuple[str, str]:
    """返回 (洞察正文, source)，source 为 llm 或 rules。"""
    if client is None:
        return generate_insights(metrics), "rules"

    try:
        return generate_llm_insights(metrics, client), "llm"
    except Exception:
        logger.exception("LLM insights failed, falling back to rules")
        return generate_insights(metrics), "rules"
