from __future__ import annotations

from pulse.domain import ParseSummary, SubmitChannelLiteral


def format_private_confirmation(name: str, period: str, summary: ParseSummary) -> str:
    cost_line = (
        "付费花费：均为 Included/Free，无按需计费"
        if summary.all_included_or_free
        else f"付费花费：${summary.total_cost_usd:.2f}"
    )
    models = "、".join(f"{m} ({n})" for m, n in summary.top_models) or "无"
    return (
        f"✅ {period} 用量已录入\n\n"
        f"事件行数：{summary.event_count}\n"
        f"Total Tokens：{summary.total_tokens:,}\n"
        f"{cost_line}\n"
        f"日期范围：{summary.date_min} ~ {summary.date_max}\n"
        f"Top 模型：{models}\n\n"
        f"Hi {name}，数据已成功保存。"
    )


def format_group_ack(name: str) -> str:
    return f"@{name} 已收到 ✅，详细结果已私聊发你。"


def format_group_submit_private_footer() -> str:
    return "小提示：下次可以直接私聊我提交，更方便也更私密。"


def format_private_confirmation_with_hint(name: str, period: str, summary: ParseSummary) -> str:
    base = format_private_confirmation(name, period, summary)
    return f"{base}\n\n{format_group_submit_private_footer()}"


def format_extraction_confidence_note(confidence: float) -> str:
    pct = f"{confidence:.0%}"
    return f"截图识别置信度：{pct}。建议下次使用 Export CSV 核对。"


def format_period_mismatch_warning(target_period: str, summary: ParseSummary) -> str:
    return (
        f"⚠️ 导出日期范围（{summary.date_min} ~ {summary.date_max}）"
        f"与目标账期 {target_period} 可能不完全匹配，请核对。"
    )
