from __future__ import annotations

from pathlib import Path

from pulse.aggregate.engine import aggregate_period
from pulse.export.exporter import export_usage_csv
from pulse.periods import current_period
from pulse.query.engine import answer_question, looks_like_query
from pulse.report.service import generate_report, publish_report_to_group
from pulse.storage.models import Member, Submission
from pulse.storage.repository import Repository
from sqlalchemy import select


def _is_admin(user_id: str, admin_ids: set[str]) -> bool:
    return user_id in admin_ids or not admin_ids


def run_command(
    text: str,
    user_id: str,
    config,
    repo: Repository,
    *,
    messenger=None,
) -> str:
    text = text.strip()
    admin_ids = set(config.admin.dingtalk_user_ids)
    is_admin = _is_admin(user_id, admin_ids)
    period_default = current_period(config)

    if text in ("状态", "/status"):
        period = period_default
        active = repo.list_active_members()
        submitted = repo.get_submitted_member_ids(period)
        lines = [f"📋 {period} 提交进度：{len(submitted)}/{len(active)}"]
        for m in active:
            mark = "✅" if m.id in submitted else "❌"
            lines.append(f"{mark} {m.display_name}")
        if not active:
            lines.append(
                "（尚未配置 active 成员；管理员用「成员 添加 userid 姓名」加入催办名单）"
            )
        return "\n".join(lines)

    if text in ("我的", "/my"):
        period = period_default
        member = repo.get_member_by_dingtalk_id(user_id)
        if not member:
            return "你还没有提交过数据。"
        sub = repo.session.scalar(
            select(Submission)
            .where(Submission.member_id == member.id, Submission.billing_period == period)
            .order_by(Submission.submitted_at.desc())
        )
        if not sub:
            return f"{period} 暂无提交记录。"
        return f"{period} 已于 {sub.submitted_at.isoformat()} 提交（{sub.submit_channel}）。"

    if text in ("帮助", "/help", "help"):
        return (
            "可用命令：\n"
            "· 状态 — 查看提交进度\n"
            "· 我的 — 查看本人提交\n"
            "· 报告 [账期] — 生成月报（管理员，发群）\n"
            "· 聚合 [账期] — 重新聚合（管理员）\n"
            "· 成员 — 成员名单（管理员）\n"
            "· 成员 添加 姓名 — 加入催办名单（管理员）\n"
            "· 查询 问题 — 如：查询 谁用得最多\n"
            "· 告警 [账期] — 运行异常检测（管理员）\n"
            "· 待审 [账期] — 查看待确认截图提交（管理员）\n"
            "· 确认 ID前缀 / 拒绝 ID前缀 — 审核截图提交\n"
            "直接发送 usage-events.csv 文件即可提交用量。"
        )

    if text.startswith("查询 ") or text.startswith("问 "):
        question = text.split(maxsplit=1)[1] if " " in text else ""
        if not question:
            return "请在「查询」后写上问题，例如：查询 谁 tokens 最多"
        result = answer_question(
            repo.session,
            question,
            user_id=user_id,
            admin_user_ids=config.admin.dingtalk_user_ids,
            config=config,
        )
        return result.answer

    if text.startswith("/aggregate") or text.startswith("聚合"):
        if not is_admin:
            return "无权限。"
        parts = text.split()
        period = parts[1] if len(parts) > 1 else period_default
        metrics = aggregate_period(repo.session, period, team_id=repo.team_id)
        return (
            f"✅ {period} 聚合完成\n"
            f"事件数：{metrics['total_events']}\n"
            f"Tokens：{metrics['total_tokens']:,}\n"
            f"付费：${metrics['total_cost_usd']:.2f}"
        )

    if text.startswith("报告") or text.startswith("/report"):
        if not is_admin:
            return "无权限。"
        if not messenger:
            return "报告发布需要 messenger，请通过 pulse serve 使用。"
        parts = text.split()
        period = parts[1] if len(parts) > 1 else period_default
        try:
            body = publish_report_to_group(
                repo.session, period, messenger, team_id=repo.team_id, config=config
            )
            return f"✅ {period} 月报已发布到群。\n\n{body[:500]}..."
        except ValueError as exc:
            return str(exc)

    if text.startswith("成员"):
        if not is_admin:
            return "无权限。"
        parts = text.split()
        if len(parts) == 1:
            active = repo.list_active_members()
            if not active:
                return "暂无 active 成员。使用「成员 添加 userid 姓名」添加。"
            lines = ["👥 成员名单（active）："]
            for m in active:
                lines.append(f"· {m.display_name} ({m.dingtalk_user_id})")
            return "\n".join(lines)
        if parts[1] == "添加" and len(parts) >= 4:
            uid, name = parts[2], parts[3]
            repo.add_member(uid, name)
            return f"已添加成员 {name}（{uid}）"
        if parts[1] == "移除" and len(parts) >= 3:
            uid = parts[2]
            member = repo.get_member_by_dingtalk_id(uid)
            if not member:
                return f"未找到 {uid}"
            member.status = "inactive"
            return f"已将 {member.display_name} 设为 inactive"
        return "用法：成员 | 成员 添加 userid 姓名 | 成员 移除 userid"

    if text.startswith("待审"):
        if not is_admin:
            return "无权限。"
        parts = text.split()
        period = parts[1] if len(parts) > 1 else period_default
        pending = repo.list_pending_submissions(period)
        if not pending:
            return f"{period} 无待审提交。"
        lines = [f"⏳ {period} 待审提交 ({len(pending)})："]
        for sub in pending[:10]:
            member = repo.session.get(Member, sub.member_id)
            name = member.display_name if member else sub.member_id[:8]
            lines.append(f"· {sub.id[:8]} {name} ({sub.input_type})")
        return "\n".join(lines)

    if text.startswith("确认 "):
        if not is_admin:
            return "无权限。"
        prefix = text.split(maxsplit=1)[1].strip()
        sub = repo.find_submission_by_id_prefix(prefix)
        if not sub:
            return f"未找到提交 {prefix}"
        repo.confirm_submission(sub.id)
        return f"✅ 已确认提交 {sub.id[:8]}，数据已计入统计。"

    if text.startswith("拒绝 "):
        if not is_admin:
            return "无权限。"
        prefix = text.split(maxsplit=1)[1].strip()
        sub = repo.find_submission_by_id_prefix(prefix)
        if not sub:
            return f"未找到提交 {prefix}"
        repo.reject_submission(sub.id)
        return f"已拒绝并删除提交 {sub.id[:8]}。"

    if text.startswith("告警") or text.startswith("/alerts"):
        if not is_admin:
            return "无权限。"
        from pulse.alerts.service import run_anomaly_check

        parts = text.split()
        period = parts[1] if len(parts) > 1 else period_default
        rows = run_anomaly_check(repo.session, config, repo.team_id, period)
        if not rows:
            return f"{period} 未检测到异常。"
        lines = [f"⚠️ {period} 检测到 {len(rows)} 条告警："]
        for row in rows[:10]:
            prefix = "🔴" if row.severity == "critical" else "🟡"
            lines.append(f"{prefix} {row.message}")
        return "\n".join(lines)

    if text.startswith("导出") or text.startswith("/export"):
        if not is_admin:
            return "无权限。"
        parts = text.split()
        period = parts[1] if len(parts) > 1 else period_default
        dest = export_usage_csv(
            repo.session,
            period,
            Path(config.storage.raw_files_dir) / f"export_{period}.csv",
        )
        row_count = len(dest.read_text(encoding="utf-8-sig").splitlines()) - 1
        return f"已导出 {row_count} 行到 {dest}"

    if looks_like_query(text):
        result = answer_question(
            repo.session,
            text,
            user_id=user_id,
            admin_user_ids=config.admin.dingtalk_user_ids,
            config=config,
        )
        return result.answer

    return "未知命令。发送「帮助」查看可用命令。"
