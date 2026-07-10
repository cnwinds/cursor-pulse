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


def _format_cost_line(summary: ParseSummary) -> str:
    if summary.all_included_or_free:
        return "均为 Included/Free"
    return f"${summary.total_cost_usd:.2f}"


def format_split_period_confirmation(
    name: str,
    period_summaries: list[tuple[str, ParseSummary]],
    overall: ParseSummary,
) -> str:
    if len(period_summaries) == 1:
        period, summary = period_summaries[0]
        return format_private_confirmation(name, period, summary)

    lines = ["✅ 用量已按账期分别录入\n"]
    for period, summary in period_summaries:
        lines.append(
            f"· {period}：{summary.event_count} 行，"
            f"Tokens {summary.total_tokens:,}，付费 {_format_cost_line(summary)}"
        )
    overall_cost = _format_cost_line(overall)
    lines.append(
        f"\n合计：{overall.event_count} 行，Total Tokens {overall.total_tokens:,}，"
        f"付费 {overall_cost}"
    )
    lines.append(f"日期范围：{overall.date_min} ~ {overall.date_max}")
    lines.append(f"\nHi {name}，各账期历史数据已覆盖更新。")
    return "\n".join(lines)


def format_auto_split_notice(periods: list[str], default_period: str) -> str:
    if len(periods) > 1:
        joined = "、".join(periods)
        return f"ℹ️ 导出跨越多个账期，已自动拆分为 {joined} 并分别覆盖历史数据。"
    if len(periods) == 1 and periods[0] != default_period:
        return f"ℹ️ 数据归属账期 {periods[0]}（当前催办账期为 {default_period}）。"
    return ""


def format_pool_spend_note(
    *,
    pool_spend: float,
    reported_spend: float | None,
    estimated_included_spend: float | None,
    quota_ratio: float | None,
    unit: str = "usd",
    cursor_pools: dict | None = None,
) -> str:
    if cursor_pools:
        auto_pool = cursor_pools.get("auto_composer") or {}
        api_pool = cursor_pools.get("api") or {}
        auto_spend = float(auto_pool.get("spend_usd") or 0)
        api_spend = float(api_pool.get("spend_usd") or 0)
        if auto_spend <= 0 and api_spend <= 0:
            return ""
        ratio_line = ""
        api_ratio = api_pool.get("usage_ratio", quota_ratio)
        if api_ratio is not None:
            ratio_line = f"\n· 高级模型额度：{api_ratio}%"
        quota_usd = api_pool.get("quota_usd")
        api_quota = f" / ${quota_usd:.0f}" if quota_usd else ""
        return (
            "\n\nCursor 套内消耗（含 Token 推算）：\n"
            f"· Auto+Composer：${auto_spend:.2f}\n"
            f"· 高级模型 API：${api_spend:.2f}{api_quota}{ratio_line}\n"
            "套内金额为定价表推算，非 Cursor 账单原值。"
        )

    if not estimated_included_spend or estimated_included_spend <= 0:
        return ""
    reported = reported_spend or 0.0
    ratio_line = f"，额度使用率 {quota_ratio}%" if quota_ratio is not None else ""
    return (
        f"\n\n池子消耗（含套内 Token 推算）：{pool_spend:.2f} {unit.upper()}\n"
        f"· 超套实付：${reported:.2f}\n"
        f"· 套内推算：~${estimated_included_spend:.2f}{ratio_line}\n"
        "套内金额为定价表推算，非 Cursor 账单原值。"
    )
