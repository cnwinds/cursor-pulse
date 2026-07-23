from __future__ import annotations

from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.channels.admin_gate import is_dingtalk_admin
from pulse.ingestion.credentials import AccountEmailMismatchError, CredentialService
from pulse.ingestion.sync import CursorSyncService
from pulse.storage.models import AiAccount, Member
from pulse.tool_center.cursor_bind import resolve_bind_cursor_account
from pulse.tool_center.repository import ToolCenterRepository


def _encryption_key(config: Any) -> str:
    if config is None:
        raise ValueError("系统未配置凭证加密密钥，请联系管理员")
    creds = getattr(config, "credentials", None)
    if creds is None:
        raise ValueError("系统未配置凭证加密密钥，请联系管理员")
    key = (getattr(creds, "encryption_key", None) or "").strip()
    if not key:
        raise ValueError("系统未配置凭证加密密钥，请联系管理员")
    return key


def _admin_ids(config: Any) -> set[str]:
    admin = getattr(config, "admin", None)
    if admin is None:
        return set()
    ids = getattr(admin, "dingtalk_user_ids", None) or []
    return set(ids)


def _is_admin_member(member: Member, config: Any) -> bool:
    if member.portal_role in ("owner", "operator"):
        return True
    return is_dingtalk_admin(member.dingtalk_user_id, _admin_ids(config))


def _can_bind_account(config: Any, member: Member, account: AiAccount) -> bool:
    if _is_admin_member(member, config):
        return True
    return account.primary_member_id == member.id


def _format_success_message(
    account: AiAccount,
    cred,
    note: str | None,
    event_count: int | None,
    sync_ok: bool,
) -> str:
    lines: list[str] = []
    if note:
        lines.append(note)
    lines.append(
        f"已绑定 Cursor 账号 {account.account_identifier}（{cred.key_hint}）"
    )
    if sync_ok and event_count is not None:
        lines.append(f"同步完成，写入 {event_count} 条事件。")
    elif not sync_ok:
        lines.append("Key 已保存，但用量同步失败，请稍后重试或联系管理员。")
    return "\n".join(lines)


def handle_cursor_key_bind(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    if not request.confirmed_by:
        return CapabilityInvokeResult(
            status="failed",
            error_code="confirmation_required",
            user_message="该操作需要确认后执行",
        )

    member = session.get(Member, request.actor_member_id)
    if member is None or member.team_id != request.team_id:
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="成员不存在或无权访问",
        )

    api_key = request.arguments.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        return CapabilityInvokeResult(
            status="failed",
            error_code="invalid_arguments",
            user_message="缺少 api_key 参数",
        )
    api_key = api_key.strip()

    email_arg = request.arguments.get("email")
    email: str | None = None
    if isinstance(email_arg, str) and email_arg.strip():
        email = email_arg.strip()

    try:
        enc_key = _encryption_key(config)
    except ValueError as exc:
        return CapabilityInvokeResult(
            status="failed",
            error_code="configuration_error",
            user_message=str(exc),
        )

    is_admin = _is_admin_member(member, config)
    tool_repo = ToolCenterRepository(session, request.team_id)
    cred_service = CredentialService(session, enc_key)

    account, note = resolve_bind_cursor_account(
        member=member,
        email=email,
        api_key=api_key,
        tool_repo=tool_repo,
        cred_service=cred_service,
        is_admin=is_admin,
    )
    if not account:
        return CapabilityInvokeResult(
            status="failed",
            error_code="resolve_failed",
            user_message=note or "无法解析要绑定的 Cursor 账号",
        )

    if not _can_bind_account(config, member, account):
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="仅账号主使用人或管理员可绑定 API Key。",
        )

    try:
        cred = cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key=api_key,
            member_id=member.id,
        )
    except AccountEmailMismatchError as exc:
        session.rollback()
        return CapabilityInvokeResult(
            status="failed",
            error_code="account_email_mismatch",
            user_message=(
                f"API Key 对应 Cursor 账号 {exc.key_email}，"
                f"与台账账号 {exc.ledger_email} 不一致。"
            ),
        )
    except Exception as exc:
        session.rollback()
        return CapabilityInvokeResult(
            status="failed",
            error_code="bind_failed",
            user_message=f"绑定失败：{exc}",
        )

    sync_ok = True
    event_count: int | None = None
    try:
        sync_service = CursorSyncService(session, enc_key)
        sync_result = sync_service.sync_account(account.id, channel="capability")
        event_count = sync_result.event_count
    except Exception:
        sync_ok = False

    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "account_id": account.id,
            "email": account.account_identifier,
            "sync_ok": sync_ok,
            "event_count": event_count,
            "note": note,
            "credential_hint": (cred.key_hint if cred else None),
        },
    )
