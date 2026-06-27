from __future__ import annotations


def generate_insights(metrics: dict) -> str:
    """规则洞察层：仅基于 metrics 已有字段归纳，不生成新数字。"""
    lines = ["【洞察】"]
    names = metrics.get("member_names") or {}
    insights: list[str] = []

    events_rank = metrics.get("events_by_member") or []
    if len(events_rank) >= 2:
        top = events_rank[0]
        top_name = names.get(top["member_id"], "?")
        insights.append(f"请求数最高的是 {top_name}（{int(top['value']):,} 次）。")

    mom = metrics.get("mom_events_change_pct")
    if mom is not None:
        if mom > 20:
            insights.append(f"整体请求量环比上升 {mom:.1f}%，用量增长明显。")
        elif mom < -20:
            insights.append(f"整体请求量环比下降 {abs(mom):.1f}%。")

    models = metrics.get("events_by_model") or {}
    if models:
        top_model = next(iter(models))
        insights.append(f"最常用模型是 {top_model}。")

    unsub = metrics.get("unsubmitted_members") or []
    if unsub:
        insights.append(f"{len(unsub)} 人尚未提交，建议跟进：{'、'.join(unsub[:5])}。")

    if metrics.get("total_cost_usd", 0) == 0:
        insights.append("本期付费合计为 $0，用量均在 Included/Free 范围内。")

    if not insights:
        insights.append("本期数据正常，暂无异常提醒。")

    lines.extend(f"· {s}" for s in insights)
    return "\n".join(lines)
