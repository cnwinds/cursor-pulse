from __future__ import annotations


def generate_insights(metrics: dict) -> str:
    """规则洞察层：不展示个人姓名，仅基于汇总指标归纳。"""
    insights: list[str] = []

    mom_events = metrics.get("mom_events_change_pct")
    mom_tokens = metrics.get("mom_tokens_change_pct")
    mom_cost = metrics.get("mom_cost_change_pct")

    if mom_events is not None:
        if mom_events > 20:
            insights.append(f"整体请求数环比上升 {mom_events:.1f}%。")
        elif mom_events < -20:
            insights.append(f"整体请求数环比下降 {abs(mom_events):.1f}%。")

    if mom_tokens is not None and mom_tokens > 20:
        insights.append(f"Tokens 总量环比上升 {mom_tokens:.1f}%。")
    elif mom_tokens is not None and mom_tokens < -20:
        insights.append(f"Tokens 总量环比下降 {abs(mom_tokens):.1f}%。")

    if mom_cost is not None and mom_cost > 20:
        insights.append(f"付费金额环比上升 {mom_cost:.1f}%。")

    models = metrics.get("events_by_model") or {}
    if models:
        top_model = next(iter(models))
        insights.append(f"请求数最多的模型是 {top_model}。")

    unsubmitted = int(metrics.get("account_count_unsubmitted") or 0)
    if unsubmitted > 0:
        insights.append(f"台账内仍有 {unsubmitted} 个账号本期无用量数据，建议跟进。")

    if float(metrics.get("total_cost_usd") or 0) == 0:
        insights.append("本期付费合计为 $0，用量主要在 Included/Free 范围内。")

    if not insights:
        insights.append("本期数据正常，暂无异常提醒。")

    lines = ["### 简要洞察", ""]
    lines.extend(f"- {item}" for item in insights)
    return "\n".join(lines)
