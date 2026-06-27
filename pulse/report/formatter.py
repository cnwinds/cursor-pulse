from __future__ import annotations


def format_monthly_report(metrics: dict) -> str:
    """从事实层 metrics 生成群月报（纯模板，数字均来自 metrics）。"""
    period = metrics["period"]
    names = metrics.get("member_names") or {}
    lines = [
        f"📊 Cursor 用量月报 · {period}",
        "",
        "【总览】",
        f"· 已提交：{metrics['member_count_reported']}/{metrics['member_count_expected']} 人",
        f"· 总事件数：{metrics['total_events']:,}",
        f"· Total Tokens：{metrics['total_tokens']:,}",
        f"· 付费合计：${metrics['total_cost_usd']:.2f}",
    ]

    mom_e = metrics.get("mom_events_change_pct")
    mom_c = metrics.get("mom_cost_change_pct")
    if mom_e is not None:
        lines.append(f"· 事件数环比：{mom_e:+.1f}%")
    if mom_c is not None:
        lines.append(f"· 付费环比：{mom_c:+.1f}%")

    lines.extend(["", "【请求数排名】"])
    for row in metrics.get("events_by_member", [])[:10]:
        name = names.get(row["member_id"], row["member_id"][:8])
        lines.append(f"{row['rank']}. {name} — {int(row['value']):,} 次")

    lines.extend(["", "【Token 排名】"])
    for row in metrics.get("tokens_by_member", [])[:5]:
        name = names.get(row["member_id"], row["member_id"][:8])
        lines.append(f"{row['rank']}. {name} — {int(row['value']):,}")

    if metrics.get("total_cost_usd", 0) > 0:
        lines.extend(["", "【付费排名】"])
        for row in metrics.get("cost_by_member", [])[:5]:
            name = names.get(row["member_id"], row["member_id"][:8])
            lines.append(f"{row['rank']}. {name} — ${row['value']:.2f}")

    lines.extend(["", "【模型分布（事件数）】"])
    for model, count in list(metrics.get("events_by_model", {}).items())[:8]:
        lines.append(f"· {model}: {count:,}")

    unsub = metrics.get("unsubmitted_members") or []
    if unsub:
        lines.extend(["", "【未提交】", "、".join(unsub)])

    return "\n".join(lines)
