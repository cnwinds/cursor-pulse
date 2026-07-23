"""Structured key-loan operations (shared by text commands and capability handlers)."""

from __future__ import annotations

from datetime import date

from pulse.channels.commands_common import encryption_key
from pulse.proxy.service import loan_proxy_usage_summary
from pulse.storage.models import Member
from pulse.storage.repository import Repository
from pulse.tool_center.burn_rate import analyze_burn_rate


def _decrypt_loan_user_api_key(loan_svc, loan) -> tuple[str | None, bool]:
    """Return borrower-facing key (pka_ or cr*), never leak cursor key for alias mode."""
    from pulse.tool_center.key_loans import KeyLoanError, reveal_loan_user_key

    try:
        return reveal_loan_user_key(loan, loan_svc.encryption_key, loan_svc.session), False
    except KeyLoanError:
        return None, True
    except Exception:
        return None, True


def _loan_item_dict(loan_svc, loan) -> dict:
    from pulse.tool_center.key_loans import DELIVERY_PROXY_ALIAS, loan_payload

    base = loan_payload(loan, loan_svc.session)
    api_key, unavailable = _decrypt_loan_user_api_key(loan_svc, loan)
    approx_cents = loan_svc.approximate_borrowed_cents(loan)
    delivery_mode = base.get("delivery_mode") or "cursor_direct"

    remaining_headroom_pct: float | None = None
    snapshot = loan_svc.latest_snapshot(loan.source_account_id)
    if snapshot is not None:
        remaining_headroom_pct = analyze_burn_rate(
            snapshot, date.today()
        ).remaining_headroom_pct

    # Full loan lifetime for "我的借用" summary (from creation onward).
    proxy = loan_proxy_usage_summary(
        loan_svc.session,
        loan.id,
        start=loan.created_at,
    )
    has_proxy = int(proxy.get("request_count") or 0) > 0
    usage_source = "proxy" if has_proxy else "quota_approx"

    return {
        "loan_id": loan.id,
        "lender_name": base.get("primary_member_name"),
        "source_identifier": base.get("source_account_identifier"),
        "created_at": base.get("created_at"),
        "approx_borrowed_usd": round(approx_cents / 100.0, 2),
        "auto_revoke_on_reset": bool(loan.auto_revoke_on_reset),
        "loan_expires_on": base.get("loan_expires_on"),
        "api_key": api_key,
        "api_key_unavailable": unavailable,
        "delivery_mode": delivery_mode,
        "requires_proxy": delivery_mode == DELIVERY_PROXY_ALIAS,
        "status": loan.status,
        "usage_source": usage_source,
        "proxy_request_count": int(proxy["request_count"]) if has_proxy else 0,
        "proxy_total_tokens": int(proxy["total_tokens"]) if has_proxy else 0,
        "proxy_cost_usd": round(float(proxy["cost_usd"]), 2) if has_proxy else 0.0,
        "remaining_headroom_pct": remaining_headroom_pct,
    }


def build_self_loan_payload(repo: Repository, config, member: Member) -> dict:
    """Structured payload for key.loan.self.read (schema_version=1)."""
    from pulse.tool_center.key_loans import KeyLoanService

    try:
        enc_key = encryption_key(config)
    except ValueError as exc:
        return {
            "schema_version": 1,
            "empty_reason": "config_error",
            "error": str(exc),
            "loans": [],
            "loan": None,
        }
    loan_svc = KeyLoanService(repo.session, enc_key)
    loans = loan_svc.list_active_loans_for_borrower(member.id)
    if not loans:
        return {
            "schema_version": 1,
            "empty_reason": "no_active_loan",
            "loans": [],
            "loan": None,
        }
    items = [_loan_item_dict(loan_svc, loan) for loan in loans]
    return {
        "schema_version": 1,
        "empty_reason": None,
        "loans": items,
        # 兼容旧单条字段：取最近一条
        "loan": items[0],
    }


def read_self_loan(repo: Repository, config, member: Member) -> str:
    payload = build_self_loan_payload(repo, config, member)
    if payload.get("empty_reason") == "config_error":
        return str(payload.get("error") or "配置错误")
    items = list(payload.get("loans") or [])
    if payload.get("empty_reason") == "no_active_loan" or not items:
        return "你当前没有进行中的 Key 借用。"
    blocks: list[str] = []
    for idx, loan in enumerate(items, start=1):
        created = str(loan.get("created_at") or "")[:19].replace("T", " ")
        header = "📎 当前借用：" if len(items) == 1 else f"📎 当前借用 {idx}/{len(items)}："
        lines = [
            header,
            f"创建时间：{created}",
        ]
        if loan.get("usage_source") == "proxy":
            count = int(loan.get("proxy_request_count") or 0)
            tokens = int(loan.get("proxy_total_tokens") or 0)
            cost = float(loan.get("proxy_cost_usd") or 0)
            lines.append(
                f"用量：{count:,} 次 · {tokens:,} tokens · ≈${cost:.2f}（Proxy 精确计量）"
            )
            headroom = loan.get("remaining_headroom_pct")
            if headroom is not None:
                lines.append(f"还能用：{float(headroom):.1f}%")
        else:
            lines.append(f"近似消耗：${float(loan.get('approx_borrowed_usd') or 0):.2f}")
        lines.extend(
            [
                f"重置日自动回收：{'是' if loan.get('auto_revoke_on_reset') else '否'}",
                f"自动回收日：{loan.get('loan_expires_on') or '—'}",
            ]
        )
        if loan.get("api_key"):
            lines.append(f"Key：{loan['api_key']}")
            if loan.get("requires_proxy"):
                lines.append("（代理别名 Key，须配置 HTTPS_PROXY 后使用）")
        elif loan.get("api_key_unavailable"):
            lines.append("Key：暂无法读取，请联系管理员")
        blocks.append("\n".join(lines))
    blocks.append("归还请发送：归还 Key")
    return "\n\n".join(blocks)

def return_loan(repo: Repository, config, member: Member) -> str:
    from pulse.tool_center.key_loans import KeyLoanService

    try:
        enc_key = encryption_key(config)
    except ValueError as exc:
        return str(exc)
    loan_svc = KeyLoanService(repo.session, enc_key)
    loan = loan_svc.active_loan_for_borrower(member.id)
    if not loan:
        return "你当前没有可归还的借用。"
    try:
        loan, borrowed_cents = loan_svc.revoke_loan(loan.id, revoke_remote=True)
        repo.session.flush()
        return (
            f"✅ 已归还借用（{loan.id[:8]}），Key 已撤销。\n"
            f"近似消耗：${borrowed_cents / 100:.2f}"
        )
    except Exception as exc:
        repo.session.rollback()
        return f"归还失败：{exc}"


def request_loan(
    repo: Repository,
    config,
    member: Member,
    *,
    note: str | None = None,
) -> str:
    payload = request_loan_payload(repo, config, member, note=note)
    if payload.get("ok"):
        lender_name = payload.get("lender_name") or "—"
        return (
            "✅ 已为你分配临时 Key：\n"
            f"借出人：{lender_name}\n"
            f"Key：{payload['api_key']}\n"
            f"自动回收日：{payload.get('loan_expires_on') or '—'}\n\n"
            f"{payload.get('warning') or ''}\n"
            "归还请发送：归还 Key"
        )
    return str(payload.get("error") or "借 Key 失败")


def request_loan_payload(
    repo: Repository,
    config,
    member: Member,
    *,
    note: str | None = None,
) -> dict:
    from pulse.tool_center.key_loans import KeyLoanError, request_self_service_loan

    try:
        enc_key = encryption_key(config)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "config_error"}
    try:
        result = request_self_service_loan(
            repo.session,
            enc_key,
            team_id=repo.team_id,
            borrower=member,
            note=note,
            loan_selection=config.tool_center.loan_selection,
        )
        repo.session.flush()
        return {
            "ok": True,
            "schema_version": 1,
            "lender_name": result.get("primary_member_name"),
            "source_identifier": result.get("source_account_identifier"),
            "api_key": result.get("api_key"),
            "delivery_mode": result.get("delivery_mode") or "proxy_alias",
            "warning": result.get("warning"),
            "loan_id": result.get("loan_id"),
            "loan_expires_on": result.get("loan_expires_on"),
        }
    except KeyLoanError as exc:
        repo.session.rollback()
        return {"ok": False, "error": f"借 Key 失败：{exc}", "error_code": "loan_failed"}
    except Exception as exc:
        repo.session.rollback()
        return {"ok": False, "error": f"借 Key 失败：{exc}", "error_code": "loan_failed"}

def list_active_loans(repo: Repository, config, *, team_id: str) -> str:
    from pulse.tool_center.key_loans import KeyLoanService, loan_payload

    try:
        enc_key = encryption_key(config)
    except ValueError as exc:
        return str(exc)
    loan_svc = KeyLoanService(repo.session, enc_key)
    loans = loan_svc.list_active_loans_for_team(team_id)
    if not loans:
        return "当前没有活跃的 Key 借用。"
    lines = [f"📋 活跃借用（{len(loans)}）："]
    for loan in loans[:15]:
        payload = loan_payload(loan, repo.session)
        approx = loan_svc.approximate_borrowed_cents(loan)
        lines.append(
            f"· {loan.id[:8]} {payload['borrower_name'] or '—'} "
            f"← {payload['source_account_identifier']} "
            f"(${approx / 100:.2f})"
        )
    lines.append("撤销：撤销借用 ID前8位")
    return "\n".join(lines)


def revoke_loan(
    repo: Repository,
    config,
    *,
    loan_id_prefix: str,
    team_id: str,
) -> str:
    from pulse.tool_center.key_loans import KeyLoanService

    prefix = (loan_id_prefix or "").strip()
    loan_svc = KeyLoanService(repo.session, encryption_key(config))
    loans = loan_svc.list_active_loans_for_team(team_id)
    matched = [loan for loan in loans if loan.id.startswith(prefix)]
    if len(matched) != 1:
        return f"未找到唯一借用记录（前缀 {prefix}）"
    loan = matched[0]
    try:
        loan, borrowed_cents = loan_svc.revoke_loan(loan.id, revoke_remote=True)
        repo.session.flush()
        return f"✅ 已撤销借用 {loan.id[:8]}，近似消耗 ${borrowed_cents / 100:.2f}"
    except Exception as exc:
        repo.session.rollback()
        return f"撤销失败：{exc}"
