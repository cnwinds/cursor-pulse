"""IM notifications for key-loan lifecycle (issued / reclaimed)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy.orm import Session

from pulse.channels.base import normalize_platform, outbound_messenger_or_none
from pulse.channels.outbound_ledger import send_oto_and_ledger
from pulse.config import ProxyAddress
from pulse.ingestion.on_demand import resolve_admin_dingtalk_ids
from pulse.proxy.key_crud import build_client_command, build_client_setup_commands
from pulse.storage.models import KeyLoan, Member
from pulse.tenant.context import team_repository
from pulse.tool_center.key_loan_delivery import (
    DELIVERY_PROXY_ALIAS,
    assignment_mode_label,
)

logger = logging.getLogger(__name__)

ReclaimReason = Literal["returned", "revoked", "expired"]

_RECLAIM_REASON_LABEL = {
    "returned": "已归还",
    "revoked": "管理员撤销",
    "expired": "到期自动回收",
}


def resolve_proxy_addresses(session: Session | None, config: Any) -> list[ProxyAddress]:
    """团队系统设置中的全部有效代理地址；无则回退 config / 默认 public_url。"""
    if session is not None:
        try:
            from pulse.settings import configured_proxy_addresses

            team, _ = team_repository(session, config)
            team_addrs = configured_proxy_addresses(session, team.id)
            if team_addrs:
                return team_addrs
        except Exception:
            logger.debug("resolve_proxy_addresses: team settings unavailable", exc_info=True)

    cfg_addrs = getattr(getattr(config, "proxy_addresses", None), "addresses", None) or []
    if cfg_addrs:
        return list(cfg_addrs)

    fallback = (getattr(getattr(config, "proxy", None), "public_url", None) or "http://127.0.0.1:8317").rstrip("/")
    return [ProxyAddress(url=fallback, display_name=fallback)]


def proxy_public_url(config: Any, session: Session | None = None) -> str:
    """返回第一个代理地址（向后兼容）。"""
    addrs = resolve_proxy_addresses(session, config)
    return addrs[0].url.rstrip("/")


def build_setup_commands(*, api_key: str, proxy_url: str) -> dict[str, str]:
    return {
        "powershell": build_client_command(shell="powershell", proxy_url=proxy_url, plaintext_key=api_key),
        "bash": build_client_command(shell="bash", proxy_url=proxy_url, plaintext_key=api_key),
    }


def _menu_style_command_block(*, api_key: str, addresses: list[ProxyAddress]) -> str:
    """与 web-admin「复制命令」下拉菜单相同顺序：每代理先 PowerShell 再 Linux/macOS，均为一行命令。"""
    items = build_client_setup_commands(plaintext_key=api_key, addresses=addresses)
    parts: list[str] = []
    for item in items:
        name = str(item.get("proxy_name") or item.get("proxy_url") or "").strip()
        shell = item.get("shell")
        if shell == "powershell":
            label = f"{name} · Windows PowerShell"
        else:
            label = f"{name} · Linux / macOS"
        parts.extend([label, str(item.get("command") or ""), ""])
    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts)


def format_borrower_issued(
    *,
    api_key: str,
    loan_id: str,
    loan_expires_on: str | None,
    warning: str | None = None,
    proxy_url: str | None = None,
    addresses: list[ProxyAddress] | None = None,
    delivery_mode: str | None = None,
    lender_mode: str | None = None,
    routing_mode: str | None = None,
    assignment_label: str | None = None,
) -> str:
    if addresses:
        resolved = addresses
    elif proxy_url:
        resolved = [ProxyAddress(url=proxy_url.rstrip("/"), display_name=proxy_url.rstrip("/"))]
    else:
        resolved = [ProxyAddress(url="http://127.0.0.1:8317", display_name="http://127.0.0.1:8317")]

    cmd_block = _menu_style_command_block(api_key=api_key, addresses=resolved)
    lines = [
        "✅ 临时 Key 已生效",
        "",
        f"Key：{api_key}",
        f"借用编号：{(loan_id or '')[:8] or '—'}",
        f"自动回收日：{loan_expires_on or '—'}",
    ]
    mode = (delivery_mode or "").strip()
    if mode == DELIVERY_PROXY_ALIAS:
        label = (assignment_label or "").strip() or assignment_mode_label(
            delivery_mode=mode,
            lender_mode=lender_mode,
            routing_mode=routing_mode,
        )
        lines.append(f"分配方式：{label}（须配置 HTTPS_PROXY）")
    elif mode and mode != DELIVERY_PROXY_ALIAS:
        lines.append("分配方式：Cursor Key")
    if warning:
        lines.extend(["", warning.strip()])
    lines.extend(
        [
            "",
            cmd_block,
            "",
            "归还请发送：归还 Key",
        ]
    )
    return "\n".join(lines)


def format_admin_issued(
    *,
    borrower_name: str | None,
    loan_id: str,
    loan_expires_on: str | None,
    delivery_mode: str | None = None,
) -> str:
    return "\n".join(
        [
            "📥 Key 借用已生效",
            f"借用人：{borrower_name or '—'}",
            f"借用编号：{(loan_id or '')[:8] or '—'}",
            f"自动回收日：{loan_expires_on or '—'}",
            f"交付模式：{delivery_mode or '—'}",
        ]
    )


def format_borrower_reclaimed(
    *,
    loan_id: str,
    reason: ReclaimReason,
    borrowed_cents: int = 0,
) -> str:
    return "\n".join(
        [
            "🔔 临时 Key 已回收",
            f"借用编号：{(loan_id or '')[:8] or '—'}",
            f"原因：{_RECLAIM_REASON_LABEL.get(reason, reason)}",
            f"借用消耗：${borrowed_cents / 100:.2f}",
            "该 Key 已失效，请勿继续使用。",
        ]
    )


def format_admin_reclaimed(
    *,
    borrower_name: str | None,
    loan_id: str,
    reason: ReclaimReason,
    borrowed_cents: int = 0,
) -> str:
    return "\n".join(
        [
            "📤 Key 借用已回收",
            f"借用人：{borrower_name or '—'}",
            f"借用编号：{(loan_id or '')[:8] or '—'}",
            f"原因：{_RECLAIM_REASON_LABEL.get(reason, reason)}",
            f"借用消耗：${borrowed_cents / 100:.2f}",
        ]
    )


def resolve_member_im_user_id(session: Session, config: Any, member: Member) -> str | None:
    """Resolve borrower/admin IM user id when the member has bound a channel identity."""
    from pulse.identity.service import external_id_for

    platform = normalize_platform(getattr(getattr(config, "bot", None), "name", None))
    if platform in ("dingtalk", "feishu"):
        uid = external_id_for(session, member, platform)
        if uid:
            return uid.strip() or None
    for channel in ("dingtalk", "feishu"):
        uid = external_id_for(session, member, channel)
        if uid:
            return uid.strip() or None
    if (member.channel or "").strip().lower() in ("dingtalk", "feishu"):
        value = (member.channel_user_id or "").strip()
        return value or None
    return None


def _send_oto(
    session: Session,
    config: Any,
    messenger: Any,
    user_id: str,
    text: str,
    *,
    source: str,
    context: str,
) -> None:
    try:
        team_id = None
        try:
            team, _ = team_repository(session, config)
            team_id = team.id
        except Exception:
            logger.exception(
                "key loan notify: team resolve failed (%s); sending without ledger",
                context,
            )
        send_oto_and_ledger(
            config,
            messenger,
            user_id=user_id,
            text=text,
            source=source,
            team_id=team_id,
            session=session if team_id else None,
        )
    except Exception:
        logger.exception("key loan IM notify failed (%s) user=%s", context, user_id)


def notify_loan_issued(
    session: Session,
    config: Any,
    *,
    result: dict[str, Any],
    skip_borrower: bool = False,
) -> None:
    """Push issued notification. Never includes lender / account-owner identity to borrower."""
    messenger = outbound_messenger_or_none(config)
    if messenger is None:
        return

    loan_id = str(result.get("loan_id") or "")
    api_key = str(result.get("api_key") or "")
    expires = result.get("loan_expires_on")
    expires_s = str(expires) if expires else None
    delivery_mode = result.get("delivery_mode")
    lender_mode = result.get("lender_mode")
    routing_mode = result.get("routing_mode")
    assignment_label = result.get("assignment_label")
    warning = result.get("warning")
    borrower_name = result.get("borrower_name")
    addresses = resolve_proxy_addresses(session, config)

    if not skip_borrower and api_key:
        borrower_id = result.get("borrower_member_id")
        borrower = session.get(Member, borrower_id) if borrower_id else None
        loan_row = None
        if loan_id:
            loan_row = session.get(KeyLoan, loan_id)
        if borrower is None and loan_row and loan_row.borrower_member_id:
            borrower = session.get(Member, loan_row.borrower_member_id)
        if loan_row is not None:
            if lender_mode is None:
                lender_mode = getattr(loan_row, "lender_mode", None)
            if routing_mode is None:
                routing_mode = getattr(loan_row, "routing_mode", None)
            if not assignment_label:
                assignment_label = assignment_mode_label(
                    delivery_mode=str(delivery_mode or getattr(loan_row, "delivery_mode", None)),
                    lender_mode=lender_mode,
                    routing_mode=routing_mode,
                )
        if borrower is not None:
            uid = resolve_member_im_user_id(session, config, borrower)
            if uid:
                text = format_borrower_issued(
                    api_key=api_key,
                    loan_id=loan_id,
                    loan_expires_on=expires_s,
                    warning=str(warning) if warning else None,
                    addresses=addresses,
                    delivery_mode=str(delivery_mode) if delivery_mode else None,
                    lender_mode=str(lender_mode) if lender_mode else None,
                    routing_mode=str(routing_mode) if routing_mode else None,
                    assignment_label=str(assignment_label) if assignment_label else None,
                )
                _send_oto(
                    session,
                    config,
                    messenger,
                    uid,
                    text,
                    source="key_loan.issued",
                    context="issued-borrower",
                )
            else:
                logger.info("key loan issued: borrower has no IM identity loan=%s", loan_id[:8])

    admin_text = format_admin_issued(
        borrower_name=str(borrower_name) if borrower_name else None,
        loan_id=loan_id,
        loan_expires_on=expires_s,
        delivery_mode=str(delivery_mode) if delivery_mode else None,
    )
    for admin_uid in resolve_admin_dingtalk_ids(config):
        _send_oto(
            session,
            config,
            messenger,
            admin_uid,
            admin_text,
            source="key_loan.issued",
            context="issued-admin",
        )


def format_admin_reassigned(
    *,
    borrower_name: str | None,
    loan_id: str,
    old_source_identifier: str | None,
    new_source_identifier: str | None,
    loan_expires_on: str | None,
    alias_key_hint: str | None = None,
) -> str:
    return "\n".join(
        [
            "🔁 Key 借用已更换出借账号",
            f"借用人：{borrower_name or '—'}",
            f"借用编号：{(loan_id or '')[:8] or '—'}",
            f"别名提示：{alias_key_hint or '—'}",
            f"原账号：{old_source_identifier or '—'}",
            f"新账号：{new_source_identifier or '账号池（使用中轮换）'}",
            f"自动回收日：{loan_expires_on or '—'}",
            "借用人 pka_ 不变，可透明继续使用。",
        ]
    )


def notify_loan_reassigned(
    session: Session,
    config: Any,
    *,
    result: dict[str, Any],
) -> None:
    """Admin-only IM notify after lender reassignment (borrower not notified)."""
    messenger = outbound_messenger_or_none(config)
    if messenger is None:
        return
    text = format_admin_reassigned(
        borrower_name=result.get("borrower_name"),
        loan_id=str(result.get("loan_id") or ""),
        old_source_identifier=result.get("old_source_account_identifier"),
        new_source_identifier=result.get("source_account_identifier"),
        loan_expires_on=result.get("loan_expires_on"),
        alias_key_hint=result.get("alias_key_hint") or result.get("key_hint"),
    )
    for admin_uid in resolve_admin_dingtalk_ids(config):
        _send_oto(
            session,
            config,
            messenger,
            admin_uid,
            text,
            source="key_loan.reassigned",
            context="reassigned-admin",
        )


def notify_loan_reclaimed(
    session: Session,
    config: Any,
    *,
    loan: KeyLoan,
    borrowed_cents: int = 0,
    reason: ReclaimReason,
    skip_borrower: bool = False,
) -> None:
    messenger = outbound_messenger_or_none(config)
    if messenger is None:
        return

    borrower_name = None
    borrower = None
    if loan.borrower_member_id:
        borrower = session.get(Member, loan.borrower_member_id)
        borrower_name = borrower.display_name if borrower else None

    if not skip_borrower and borrower is not None:
        uid = resolve_member_im_user_id(session, config, borrower)
        if uid:
            text = format_borrower_reclaimed(
                loan_id=loan.id,
                reason=reason,
                borrowed_cents=borrowed_cents,
            )
            _send_oto(
                session,
                config,
                messenger,
                uid,
                text,
                source="key_loan.reclaimed",
                context="reclaimed-borrower",
            )
        else:
            logger.info("key loan reclaimed: borrower has no IM identity loan=%s", loan.id[:8])

    admin_text = format_admin_reclaimed(
        borrower_name=borrower_name,
        loan_id=loan.id,
        reason=reason,
        borrowed_cents=borrowed_cents,
    )
    for admin_uid in resolve_admin_dingtalk_ids(config):
        _send_oto(
            session,
            config,
            messenger,
            admin_uid,
            admin_text,
            source="key_loan.reclaimed",
            context="reclaimed-admin",
        )
