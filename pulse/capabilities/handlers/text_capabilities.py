"""Dedicated capability handlers (Phase C)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.aggregate.engine import aggregate_period
from pulse.capabilities.handlers.common import (
    _fail,
    _success,
    is_channel_admin,
    repository_for,
    resolve_actor_member,
)
from pulse.channels.commands import handle_unbind_cursor_command
from pulse.export.exporter import export_usage_csv
from pulse.periods import current_period
from pulse.report.service import publish_report_to_group
from pulse.storage.models import UsageIngestion

logger = logging.getLogger(__name__)


def _text_arg(request: CapabilityInvokeRequest) -> str:
    return str(request.arguments.get("text") or "").strip()


def _period_arg(request: CapabilityInvokeRequest, config: Any) -> str:
    period = request.arguments.get("period")
    if isinstance(period, str) and period.strip():
        return period.strip()
    text = _text_arg(request)
    parts = text.split()
    if len(parts) > 1:
        return parts[1]
    return current_period(config)


def _optional_messenger(config: Any):
    dingtalk = getattr(config, "dingtalk", None)
    if dingtalk and getattr(dingtalk, "app_key", None) and getattr(dingtalk, "app_secret", None):
        try:
            from pulse.channels.dingtalk.messenger import DingTalkMessenger

            return DingTalkMessenger(config)
        except Exception:
            return None
    return None


def handle_bot_help(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    from assistant_platform.conversation.help import build_help_message_from_keys

    allowed = request.arguments.get("allowed_capability_keys")
    topic = request.arguments.get("topic")
    topic_str = str(topic) if topic else None
    if isinstance(allowed, list) and allowed:
        return _success(
            build_help_message_from_keys(
                allowed,
                topic=topic_str,
                member_id=request.actor_member_id,
            ),
            capability_key="bot.help",
        )
    from pulse.channels.commands import build_bot_help_message

    return _success(
        build_bot_help_message(topic=topic_str),
        capability_key="bot.help",
    )


def handle_submission_self_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    period = _period_arg(request, config)
    sub = session.scalar(
        select(UsageIngestion)
        .where(UsageIngestion.member_id == member.id, UsageIngestion.billing_period == period)
        .order_by(UsageIngestion.ingested_at.desc())
    )
    if not sub:
        return _success(f"{period} 暂无提交记录。", capability_key="submission.self.read")
    return _success(
        f"{period} 已于 {sub.ingested_at.isoformat()} 提交（{sub.channel}）。",
        capability_key="submission.self.read",
    )


def handle_submission_status_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    period = _period_arg(request, config)
    active = repo.list_active_members()
    submitted = repo.get_submitted_member_ids(period)
    lines = [f"📋 {period} 提交进度：{len(submitted)}/{len(active)}"]
    for m in active:
        mark = "✅" if m.id in submitted else "❌"
        lines.append(f"{mark} {m.display_name}")
    if not active:
        lines.append("（尚未配置 active 成员；管理员用「成员 添加 userid 姓名」加入催办名单）")
    return _success("\n".join(lines), capability_key="submission.status.read")


def handle_cursor_key_unbind(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    text = _text_arg(request)
    if not text:
        email = request.arguments.get("email")
        if isinstance(email, str) and email.strip():
            text = f"解绑 cursor {email.strip()}"
        else:
            text = "解绑 cursor"
    reply = handle_unbind_cursor_command(
        text,
        member.dingtalk_user_id,
        config,
        repo,
        display_name=member.display_name,
    )
    if reply is None:
        return _fail("invalid_arguments", "无法解析解绑命令")
    return _success(reply, capability_key="cursor.key.unbind")


def handle_usage_manual_submit(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    from pulse.tool_center.manual import ManualUsageService, parse_manual_usage_text

    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    text = _text_arg(request)
    if not text:
        return _fail("invalid_arguments", "缺少上报内容")
    period = current_period(config)
    try:
        command = parse_manual_usage_text(text)
        svc = ManualUsageService(session, request.team_id)
        ingestion, account, summary = svc.submit_for_member(
            member=member,
            period=period,
            command=command,
            submit_channel="capability",
            repo=repo,
        )
        session.flush()
        vendor_name = account.vendor.name if account.vendor else command.vendor_slug
        ratio = summary.get("quota_usage_ratio")
        ratio_line = f"\n额度使用率：{ratio}%" if ratio is not None else ""
        reply = (
            f"✅ {period} {vendor_name} 用量已入库\n"
            f"账号：{account.account_identifier}\n"
            f"主指标：{summary['primary_metric_value']} {summary['primary_metric_unit'].upper()}"
            f"{ratio_line}\n"
            f"已计入统计，发送「我的」可查看提交记录。"
        )
        return _success(reply, capability_key="usage.manual.submit")
    except ValueError as exc:
        return _fail("invalid_arguments", f"上报失败：{exc}")


def handle_usage_aggregate(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    period = _period_arg(request, config)
    metrics = aggregate_period(session, period, team_id=request.team_id)
    reply = (
        f"✅ {period} 聚合完成\n"
        f"事件数：{metrics['total_events']}\n"
        f"Tokens：{metrics['total_tokens']:,}\n"
        f"付费：${metrics['total_cost_usd']:.2f}"
    )
    return _success(reply, capability_key="usage.aggregate")


def handle_report_publish(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    period = _period_arg(request, config)
    messenger = _optional_messenger(config)
    try:
        from pulse.report.service import should_publish_report_to_group

        if should_publish_report_to_group(config) and not messenger:
            return _fail("configuration_error", "报告发布需要 messenger，请通过 pulse channel 使用。")
        body = publish_report_to_group(
            session, period, messenger, team_id=request.team_id, config=config
        )
        if should_publish_report_to_group(config):
            return _success(
                f"✅ {period} 月报已发布到群。\n\n{body[:500]}...",
                capability_key="report.publish",
            )
        preview = body if len(body) <= 3500 else f"{body[:3500]}\n\n…（内容过长已截断）"
        return _success(
            f"✅ {period} 月报已生成（暂未发群，仅预览）：\n\n{preview}",
            capability_key="report.publish",
        )
    except ValueError as exc:
        return _fail("report_failed", str(exc))


def handle_members_manage(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    text = _text_arg(request) or "成员"
    parts = text.split()
    if len(parts) == 1:
        active = repo.list_active_members()
        if not active:
            return _success("暂无 active 成员。使用「成员 添加 userid 姓名」添加。", capability_key="members.manage")
        lines = ["👥 成员名单（active）："]
        for m in active:
            lines.append(f"· {m.display_name} ({m.dingtalk_user_id})")
        return _success("\n".join(lines), capability_key="members.manage")
    if parts[1] == "添加" and len(parts) >= 4:
        uid, name = parts[2], parts[3]
        repo.add_member(uid, name)
        return _success(f"已添加成员 {name}（{uid}）", capability_key="members.manage")
    if parts[1] == "移除" and len(parts) >= 3:
        uid = parts[2]
        target = repo.get_member_by_dingtalk_id(uid)
        if not target:
            return _fail("not_found", f"未找到 {uid}")
        target.status = "inactive"
        return _success(f"已将 {target.display_name} 设为 inactive", capability_key="members.manage")
    return _fail("invalid_arguments", "用法：成员 | 成员 添加 userid 姓名 | 成员 移除 userid")


def handle_alerts_run(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    from pulse.alerts.service import run_anomaly_check

    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    period = _period_arg(request, config)
    rows = run_anomaly_check(session, config, request.team_id, period)
    if not rows:
        return _success(f"{period} 未检测到异常。", capability_key="alerts.run")
    lines = [f"⚠️ {period} 检测到 {len(rows)} 条告警："]
    for row in rows[:10]:
        prefix = "🔴" if row.severity == "critical" else "🟡"
        lines.append(f"{prefix} {row.message}")
    return _success("\n".join(lines), capability_key="alerts.run")


def handle_usage_export(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return _fail("forbidden", "成员不存在或无权访问")
    repo = repository_for(session, request.team_id)
    if not is_channel_admin(member.dingtalk_user_id, config, repo):
        return _fail("forbidden", "无权限。")
    period = _period_arg(request, config)
    dest = export_usage_csv(
        session,
        period,
        Path(config.storage.raw_files_dir) / f"export_{period}.csv",
    )
    row_count = len(dest.read_text(encoding="utf-8-sig").splitlines()) - 1
    return _success(f"已导出 {row_count} 行到 {dest}", capability_key="usage.export")
