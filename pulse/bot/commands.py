from __future__ import annotations

from pathlib import Path

from pulse.aggregate.engine import aggregate_period
from pulse.export.exporter import export_usage_csv
from pulse.periods import current_period
from pulse.query.engine import answer_question, looks_like_query
from pulse.report.service import generate_report, publish_report_to_group
from pulse.storage.models import Member, UsageIngestion
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
            select(UsageIngestion)
            .where(UsageIngestion.member_id == member.id, UsageIngestion.billing_period == period)
            .order_by(UsageIngestion.ingested_at.desc())
        )
        if not sub:
            return f"{period} 暂无提交记录。"
        return f"{period} 已于 {sub.ingested_at.isoformat()} 提交（{sub.channel}）。"

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
            "· 申请 Cursor — 提交 AI 工具试用申请\n"
            "· 审批 通过 ID前缀 / 审批 拒绝 ID前缀 — 主管审批申请\n"
            "· 心得：你的技巧 — 分享本月 AI 使用心得（私聊或群内）\n"
            "· 上报 智谱 85 — 非 Cursor 工具手工上报用量\n"
            "· 智谱/MiniMax/Codex 可发控制台截图自动识别\n"
            "直接发送 usage-events.csv 或 .xlsx 文件即可提交 Cursor 用量。\n"
            "若有多个 Cursor 账号，上传后请按提示回复序号或邮箱指定账号。"
        )

    if looks_like_manual_usage(text):
        from pulse.tool_center.manual import ManualUsageService, parse_manual_usage_text

        member = repo.get_or_create_member(user_id, user_id)
        try:
            command = parse_manual_usage_text(text)
            svc = ManualUsageService(repo.session, repo.team_id)
            submission, account, summary = svc.submit_for_member(
                member=member,
                period=period_default,
                command=command,
                submit_channel="dingtalk",
                repo=repo,
                upgrade_notify=(
                    messenger.send_oto_text,
                    list(config.admin.dingtalk_user_ids),
                )
                if messenger and config.admin.dingtalk_user_ids
                else None,
            )
            repo.session.flush()
            vendor_name = account.vendor.name if account.vendor else command.vendor_slug
            ratio = summary.get("quota_usage_ratio")
            ratio_line = f"\n额度使用率：{ratio}%" if ratio is not None else ""
            return (
                f"✅ {period_default} {vendor_name} 用量已记录\n"
                f"账号：{account.account_identifier}\n"
                f"主指标：{summary['primary_metric_value']} {summary['primary_metric_unit'].upper()}"
                f"{ratio_line}"
            )
        except ValueError as exc:
            return f"上报失败：{exc}"

    if text.startswith("申请"):
        from pulse.tool_center.requests import AccessRequestError, AccessRequestService
        from pulse.tool_center.repository import ToolCenterRepository

        member = repo.get_or_create_member(user_id, user_id)
        tool_repo = ToolCenterRepository(repo.session, repo.team_id)
        vendor = tool_repo.get_vendor_by_slug("cursor")
        if not vendor:
            return "系统尚未配置 Cursor 厂商，请联系管理员执行 pulse init-v2 --seed"
        reason = text[len("申请") :].strip() or None
        svc = AccessRequestService(repo.session, repo.team_id)
        try:
            row = svc.create_draft(applicant=member, vendor_id=vendor.id, reason=reason)
            action = svc.submit(row.id, member)
            repo.session.flush()
            extra = ""
            if row.manager_member_id:
                mgr = repo.session.get(Member, row.manager_member_id)
                if mgr:
                    extra = f"\n已通知主管 {mgr.display_name} 审批（请主管回复：审批 通过 {row.id[:8]}）"
            return action.message + extra
        except AccessRequestError as exc:
            return f"申请失败：{exc}"

    if text.startswith("审批 "):
        from pulse.tool_center.requests import AccessRequestError, AccessRequestService

        parts = text.split()
        if len(parts) < 3:
            return "用法：审批 通过/拒绝 申请ID前8位"
        decision, prefix = parts[1], parts[2]
        member = repo.get_member_by_dingtalk_id(user_id)
        if not member:
            return "未找到你的成员记录。"
        svc = AccessRequestService(repo.session, repo.team_id)
        rows = svc.list_requests(status="pending_manager", admin_view=True)
        matched = [r for r in rows if r.id.startswith(prefix)]
        if len(matched) != 1:
            return f"未找到唯一申请（前缀 {prefix}）"
        row = matched[0]
        is_admin_user = _is_admin(user_id, admin_ids)
        try:
            if decision in ("通过", "approve"):
                action = svc.approve(row.id, member, is_admin=is_admin_user)
            elif decision in ("拒绝", "reject"):
                action = svc.reject(row.id, member, is_admin=is_admin_user)
            else:
                return "决策须为 通过 或 拒绝"
            repo.session.flush()
            if action.request.status == "approved" and is_admin_user:
                try:
                    assign = svc.assign_trial(row.id)
                    repo.session.flush()
                    return assign.message
                except AccessRequestError:
                    return action.message + "\n（暂无空闲试用账号，请管理员在后台分配）"
            return action.message
        except AccessRequestError as exc:
            return f"审批失败：{exc}"

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
        pending = repo.list_pending_ingestions(period)
        if not pending:
            return f"{period} 无待审摄取。"
        lines = [f"⏳ {period} 待审摄取 ({len(pending)})："]
        for ing in pending[:10]:
            member = repo.session.get(Member, ing.member_id)
            name = member.display_name if member else (ing.member_id or "")[:8]
            lines.append(f"· {ing.id[:8]} {name} ({ing.source_type})")
        return "\n".join(lines)

    if text.startswith("确认 "):
        if not is_admin:
            return "无权限。"
        prefix = text.split(maxsplit=1)[1].strip()
        ing = repo.find_ingestion_by_id_prefix(prefix)
        if not ing:
            return f"未找到摄取 {prefix}"
        repo.confirm_ingestion(ing.id)
        return f"✅ 已确认摄取 {ing.id[:8]}，数据已计入统计。"

    if text.startswith("拒绝 "):
        if not is_admin:
            return "无权限。"
        prefix = text.split(maxsplit=1)[1].strip()
        ing = repo.find_ingestion_by_id_prefix(prefix)
        if not ing:
            return f"未找到摄取 {prefix}"
        repo.reject_ingestion(ing.id)
        return f"已拒绝并删除摄取 {ing.id[:8]}。"

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
