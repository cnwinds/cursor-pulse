from __future__ import annotations

from sqlalchemy.orm import Session

from pulse.storage.models import AiAccount, Member
from pulse.tool_center.aggregate import aggregate_account_metrics


def format_upgrade_alert(session: Session, account: AiAccount, period: str) -> str:
    primary_name = "未指定"
    if account.primary_member_id:
        member = session.get(Member, account.primary_member_id)
        if member:
            primary_name = member.display_name

    manager_line = ""
    if account.primary_member_id:
        primary = session.get(Member, account.primary_member_id)
        if primary and primary.manager_member_id:
            manager = session.get(Member, primary.manager_member_id)
            if manager:
                manager_line = f"直属主管：{manager.display_name}\n"

    plan_name = account.plan.plan_name if account.plan else "未知套餐"
    return (
        f"📌 AI 工具升级建议 · {period}\n\n"
        f"账号：{account.account_identifier}\n"
        f"套餐：{plan_name}\n"
        f"主使用人：{primary_name}\n"
        f"{manager_line}"
        f"已连续 2 个月额度使用率 ≥95%，建议申请公司独立 Cursor Pro 账号。\n"
        f"请在管理后台处理账号分配。"
    )


def build_manager_briefing(session: Session, period: str, *, team_id: str) -> str:
    metrics = aggregate_account_metrics(session, period, team_id=team_id)
    lines = [
        f"📊 AI 工具用量简报 · {period}",
        "",
        f"账号上报：{metrics['account_count_submitted']}/{metrics['account_count_active']}",
    ]
    if metrics["account_count_missing_primary"]:
        lines.append(f"待指定主使用人：{metrics['account_count_missing_primary']} 个")
    if metrics["account_count_suggest_dedicated"]:
        lines.append(f"建议升级独立账号：{metrics['account_count_suggest_dedicated']} 个")

    for bucket in metrics["by_vendor_currency"]:
        lines.append("")
        lines.append(f"【{bucket['vendor_name']} · {bucket['currency']}】")
        lines.append(f"  合计：{bucket['total_usage']:.2f} {bucket['currency']}")
        if bucket.get("avg_quota_ratio") is not None:
            lines.append(f"  平均额度使用率：{bucket['avg_quota_ratio']}%")

    if metrics.get("mom_total_usage_change_pct") is not None:
        sign = "+" if metrics["mom_total_usage_change_pct"] >= 0 else ""
        lines.append("")
        lines.append(f"环比上月总用量：{sign}{metrics['mom_total_usage_change_pct']}%")

    lines.append("")
    lines.append("详细数据请登录 Web 管理后台查看。")
    return "\n".join(lines)


def build_anonymous_group_digest(session: Session, period: str, *, team_id: str) -> str:
    metrics = aggregate_account_metrics(session, period, team_id=team_id)
    lines = [
        f"📈 {period} AI 工具使用快报（团队汇总）",
        "",
        f"✅ 账号上报完成度：{metrics['account_count_submitted']}/{metrics['account_count_active']}",
    ]

    if metrics["model_family_pct"]:
        lines.append("")
        lines.append("模型使用分布（匿名）：")
        for family, pct in metrics["model_family_pct"].items():
            lines.append(f"  · {family}：{pct}%")

    for bucket in metrics["by_vendor_currency"]:
        mom = metrics.get("mom_total_usage_change_pct")
        mom_text = ""
        if mom is not None:
            sign = "+" if mom >= 0 else ""
            mom_text = f"（环比 {sign}{mom}%）"
        lines.append("")
        lines.append(
            f"{bucket['vendor_name']}（{bucket['currency']}）"
            f"本月合计 {bucket['total_usage']:.2f}{mom_text}"
        )
        if bucket.get("avg_quota_ratio") is not None:
            lines.append(f"  试用/共享账号平均额度使用率：{bucket['avg_quota_ratio']}%")

    if metrics["account_count_suggest_dedicated"]:
        lines.append("")
        lines.append(
            f"💡 有 {metrics['account_count_suggest_dedicated']} 个账号达到升级评估条件，"
            "管理员将跟进独立账号申请。"
        )

    lines.append("")
    lines.append("继续分享你的 AI 使用技巧，私聊小脉或群内 @小脉 即可～")
    return "\n".join(lines)
