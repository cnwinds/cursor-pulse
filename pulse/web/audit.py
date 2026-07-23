from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pulse.storage.models import (
    AccessRequest,
    AdminAuditLog,
    AiAccount,
    KeyLoan,
    KnowledgeEntry,
    Member,
)
from pulse.web.permissions import PORTAL_ROLE_LABELS

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)

ACTION_LABELS: dict[str, str] = {
    "account.create": "创建账号",
    "account.update": "更新账号",
    "account.delete": "删除账号",
    "account.recompute_summary": "重算用量汇总",
    "credential.bind": "绑定密钥",
    "credential.revoke": "撤销密钥",
    "credential.sync": "同步用量",
    "quota.loan_key": "外借密钥",
    "quota.revoke_loan": "收回外借",
    "usage.manual_submit": "手动提交用量",
    "access_request.approve": "批准工具申请",
    "access_request.assign_trial": "分配试用账号",
    "dingtalk.directory_sync": "同步钉钉通讯录",
    "dingtalk.work_group.bind": "绑定钉钉工作群",
    "knowledge.update": "更新知识库",
    "knowledge.publish_digest": "发布知识摘要",
    "pricing.cursor_update": "更新 Cursor 定价表",
    "pricing.cursor_reset": "恢复 Cursor 定价表默认",
    "portal.user.approve": "批准门户用户",
    "portal.user.reject": "拒绝门户申请",
    "portal.user.disable": "禁用门户用户",
    "portal.user.delete": "删除门户用户",
}

CHANNEL_LABELS: dict[str, str] = {
    "web": "网页后台",
    "dingtalk": "钉钉",
}

CAPABILITY_LABELS: dict[str, str] = {
    "accounts:read": "账号查看",
    "accounts:write": "账号管理",
    "admin:users": "用户管理",
    "requests:approve": "审批申请",
    "knowledge:write": "知识库编辑",
    "reports:publish": "报告发布",
}

DELETE_MODE_LABELS = {
    "soft": "保留历史（软删除）",
    "hard": "彻底删除",
}


@dataclass
class _AuditContext:
    members_by_id: dict[str, Member] = field(default_factory=dict)
    members_by_dingtalk: dict[str, Member] = field(default_factory=dict)
    accounts: dict[str, AiAccount] = field(default_factory=dict)
    loans: dict[str, KeyLoan] = field(default_factory=dict)
    requests: dict[str, AccessRequest] = field(default_factory=dict)
    knowledge: dict[str, KnowledgeEntry] = field(default_factory=dict)


def log_admin_action(
    session: Session,
    *,
    team_id: str,
    member_id: str | None,
    action: str,
    capability: str | None = None,
    detail: str | None = None,
    channel: str = "web",
) -> AdminAuditLog:
    row = AdminAuditLog(
        team_id=team_id,
        member_id=member_id,
        channel=channel,
        action=action,
        capability=capability,
        detail=detail,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def _member_name(ctx: _AuditContext, member_id: str | None) -> str | None:
    if not member_id:
        return None
    member = ctx.members_by_id.get(member_id)
    return member.display_name if member else None


def _member_by_dingtalk(ctx: _AuditContext, dingtalk_user_id: str) -> Member | None:
    return ctx.members_by_dingtalk.get(dingtalk_user_id)


def _account_label(ctx: _AuditContext, account_id: str) -> str:
    account = ctx.accounts.get(account_id)
    if account and account.account_identifier:
        return account.account_identifier
    if account:
        return f"账号 {account_id[:8]}…"
    return f"账号 {account_id[:8]}…"


def _request_label(ctx: _AuditContext, request_id: str) -> str:
    req = ctx.requests.get(request_id)
    if not req:
        return f"申请 {request_id[:8]}…"
    applicant = _member_name(ctx, req.applicant_member_id) or "未知申请人"
    return f"{applicant} 的工具申请"


def _loan_label(ctx: _AuditContext, loan_id: str) -> str:
    loan = ctx.loans.get(loan_id)
    if not loan:
        return f"外借记录 {loan_id[:8]}…"
    account = _account_label(ctx, loan.source_account_id)
    borrower = _member_name(ctx, loan.borrower_member_id) or loan.borrower_note or "未知借用人"
    return f"从 {account} 借给 {borrower}"


def _knowledge_label(ctx: _AuditContext, entry_id: str) -> str:
    entry = ctx.knowledge.get(entry_id)
    if entry and entry.title:
        return f"「{entry.title}」"
    return f"条目 {entry_id[:8]}…"


def _role_label(role: str) -> str:
    return PORTAL_ROLE_LABELS.get(role, role)


def format_audit_detail(action: str, detail: str | None, ctx: _AuditContext) -> str:
    if not detail:
        return "—"

    if action == "credential.sync":
        match = re.match(r"^(.+?):(\d+)\s+events$", detail)
        if match:
            account_id, count = match.groups()
            return f"同步 {_account_label(ctx, account_id)}，拉取 {count} 条用量事件"
        return detail

    if action == "credential.bind":
        if ":" in detail:
            account_id, key_hint = detail.split(":", 1)
            return f"为 {_account_label(ctx, account_id)} 绑定密钥（尾号 {key_hint}）"
        return detail

    if action == "credential.revoke":
        if _UUID_RE.fullmatch(detail):
            return f"撤销 {_account_label(ctx, detail)} 的密钥"
        return detail

    if action == "quota.loan_key":
        if "->" in detail:
            account_id, borrower = detail.split("->", 1)
            return f"将 {_account_label(ctx, account_id)} 的密钥借给 {borrower.strip()}"
        return detail

    if action == "quota.revoke_loan":
        if _UUID_RE.fullmatch(detail):
            return f"收回 {_loan_label(ctx, detail)}"
        return detail

    if action == "account.delete":
        if ":" in detail:
            account_id, mode = detail.rsplit(":", 1)
            mode_label = DELETE_MODE_LABELS.get(mode, mode)
            return f"删除 {_account_label(ctx, account_id)}（{mode_label}）"
        return detail

    if action in {"account.update", "account.recompute_summary", "usage.manual_submit"}:
        if ":" in detail:
            account_id, extra = detail.split(":", 1)
            account = _account_label(ctx, account_id)
            if action == "account.update":
                return f"更新 {account}"
            if action == "account.recompute_summary":
                return f"重算 {account} 在账期 {extra} 的用量汇总"
            return f"手动提交 {account} 在账期 {extra} 的用量"
        if _UUID_RE.fullmatch(detail):
            return f"更新 {_account_label(ctx, detail)}"
        return detail

    if action == "account.create":
        if _UUID_RE.fullmatch(detail):
            return f"创建 {_account_label(ctx, detail)}"
        return f"创建账号 {detail}"

    if action == "portal.user.approve":
        if "->" in detail:
            dingtalk_id, role = [part.strip() for part in detail.split("->", 1)]
            member = _member_by_dingtalk(ctx, dingtalk_id)
            name = member.display_name if member else dingtalk_id
            return f"批准 {name}，角色设为 {_role_label(role)}"
        return detail

    if action in {"portal.user.reject", "portal.user.disable", "portal.user.delete"}:
        member = _member_by_dingtalk(ctx, detail)
        name = member.display_name if member else detail
        verb = {
            "portal.user.reject": "拒绝",
            "portal.user.disable": "禁用",
            "portal.user.delete": "删除",
        }[action]
        return f"{verb}门户用户 {name}"

    if action in {"access_request.approve", "access_request.assign_trial"}:
        if _UUID_RE.fullmatch(detail):
            req_label = _request_label(ctx, detail)
            if action == "access_request.approve":
                return f"批准 {req_label}"
            return f"为 {req_label} 分配试用账号"
        return detail

    if action == "dingtalk.directory_sync":
        try:
            stats = ast.literal_eval(detail)
            if isinstance(stats, dict):
                parts = []
                for key, label in (
                    ("created", "新增"),
                    ("updated", "更新"),
                    ("disabled", "禁用"),
                    ("skipped", "跳过"),
                ):
                    if key in stats:
                        parts.append(f"{label} {stats[key]}")
                if parts:
                    return "同步钉钉通讯录：" + "，".join(parts)
        except (SyntaxError, ValueError):
            pass
        return f"同步钉钉通讯录：{detail}"

    if action == "knowledge.update":
        if _UUID_RE.fullmatch(detail):
            return f"更新知识库 {_knowledge_label(ctx, detail)}"
        return detail

    if action == "knowledge.publish_digest":
        return f"发布账期 {detail} 的知识摘要"

    if action.startswith("chat.tool."):
        return detail

    uuids = _UUID_RE.findall(detail)
    if uuids:
        readable = detail
        for uid in dict.fromkeys(uuids):
            if uid in ctx.accounts:
                readable = readable.replace(uid, _account_label(ctx, uid))
            elif uid in ctx.members_by_id:
                name = ctx.members_by_id[uid].display_name
                readable = readable.replace(uid, name)
        return readable

    return detail


def action_label(action: str) -> str:
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    if action.startswith("chat.tool."):
        return f"对话工具 · {action.removeprefix('chat.tool.')}"
    return action


def _collect_uuids(*texts: str | None) -> set[str]:
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(_UUID_RE.findall(text))
    return found


def _build_audit_context(session: Session, team_id: str, rows: list[AdminAuditLog]) -> _AuditContext:
    ctx = _AuditContext()
    member_ids = {row.member_id for row in rows if row.member_id}
    uuids = set(member_ids)
    dingtalk_ids: set[str] = set()

    for row in rows:
        uuids.update(_collect_uuids(row.detail))
        if row.action == "portal.user.approve" and row.detail and "->" in row.detail:
            dingtalk_ids.add(row.detail.split("->", 1)[0].strip())
        elif row.action.startswith("portal.user.") and row.detail:
            dingtalk_ids.add(row.detail.strip())

    if uuids:
        members = session.scalars(
            select(Member).where(Member.team_id == team_id, Member.id.in_(uuids))
        ).all()
        ctx.members_by_id = {member.id: member for member in members}

    if dingtalk_ids:
        members = session.scalars(
            select(Member).where(
                Member.team_id == team_id,
                Member.dingtalk_user_id.in_(dingtalk_ids),
            )
        ).all()
        ctx.members_by_dingtalk = {member.dingtalk_user_id: member for member in members}

    account_ids = uuids - set(ctx.members_by_id)
    if account_ids:
        accounts = session.scalars(
            select(AiAccount).where(
                AiAccount.team_id == team_id,
                AiAccount.id.in_(account_ids),
            )
        ).all()
        ctx.accounts = {account.id: account for account in accounts}

    loan_ids = uuids - set(ctx.members_by_id) - set(ctx.accounts)
    if loan_ids:
        loans = session.scalars(select(KeyLoan).where(KeyLoan.id.in_(loan_ids))).all()
        ctx.loans = {loan.id: loan for loan in loans}
        for loan in loans:
            account_ids.add(loan.source_account_id)
            if loan.borrower_member_id:
                member_ids.add(loan.borrower_member_id)

    request_ids = uuids - set(ctx.members_by_id) - set(ctx.accounts) - set(ctx.loans)
    if request_ids:
        requests = session.scalars(
            select(AccessRequest).where(
                AccessRequest.team_id == team_id,
                AccessRequest.id.in_(request_ids),
            )
        ).all()
        ctx.requests = {req.id: req for req in requests}
        for req in requests:
            member_ids.add(req.applicant_member_id)

    knowledge_ids = uuids - set(ctx.members_by_id) - set(ctx.accounts) - set(ctx.loans) - set(ctx.requests)
    if knowledge_ids:
        entries = session.scalars(
            select(KnowledgeEntry).where(
                KnowledgeEntry.team_id == team_id,
                KnowledgeEntry.id.in_(knowledge_ids),
            )
        ).all()
        ctx.knowledge = {entry.id: entry for entry in entries}

    missing_member_ids = member_ids - set(ctx.members_by_id)
    if missing_member_ids:
        extra_members = session.scalars(
            select(Member).where(Member.team_id == team_id, Member.id.in_(missing_member_ids))
        ).all()
        ctx.members_by_id.update({member.id: member for member in extra_members})

    missing_account_ids = account_ids - set(ctx.accounts)
    if missing_account_ids:
        extra_accounts = session.scalars(
            select(AiAccount).where(
                or_(AiAccount.team_id == team_id, AiAccount.team_id.is_(None)),
                AiAccount.id.in_(missing_account_ids),
            )
        ).all()
        ctx.accounts.update({account.id: account for account in extra_accounts})

    return ctx


def list_admin_audit_logs(
    session: Session,
    team_id: str,
    *,
    limit: int = 100,
) -> list[dict]:
    rows = session.scalars(
        select(AdminAuditLog)
        .where(AdminAuditLog.team_id == team_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(limit)
    ).all()
    ctx = _build_audit_context(session, team_id, list(rows))
    return [
        {
            "id": row.id,
            "member_id": row.member_id,
            "operator_name": _member_name(ctx, row.member_id) or "系统",
            "channel": row.channel,
            "channel_label": CHANNEL_LABELS.get(row.channel, row.channel),
            "action": row.action,
            "action_label": action_label(row.action),
            "capability": row.capability,
            "capability_label": CAPABILITY_LABELS.get(row.capability or "", row.capability),
            "detail": format_audit_detail(row.action, row.detail, ctx),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
