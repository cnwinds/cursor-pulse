from __future__ import annotations

import logging
import re

from pulse.channels.admin_gate import is_dingtalk_admin as _is_admin
from pulse.storage.repository import Repository

from pulse.channels.commands_common import (
    can_bind_account as _can_bind_account,
    dingtalk_member as _dingtalk_member,
    encryption_key as _encryption_key,
)
from pulse.channels.commands_loans import (  # noqa: F401 — re-export for tests
    _looks_like_borrow_key_command,
    _looks_like_loan_usage_query,
    _looks_like_self_loan_read,
)
logger = logging.getLogger(__name__)

BIND_CURSOR_RE = re.compile(
    r"^绑定\s*cursor(?:\s+(?P<email>\S+@\S+))?\s+(?:key\s+)?(?P<key>crsr_\S+)$",
    re.IGNORECASE,
)
UNBIND_CURSOR_RE = re.compile(
    r"^解绑\s*cursor(?:\s+(?P<email>\S+@\S+))?$",
    re.IGNORECASE,
)

CURSOR_BIND_GUIDE = (
    "该 Cursor 账号已支持 API 自动同步，无需上传 CSV。\n\n"
    "请私聊发送：绑定 cursor key crsr_...\n"
    "若有多个账号：绑定 cursor 你的邮箱@c.com crsr_...\n\n"
    "在 Cursor Settings → Integrations 可创建 User API Key。"
)


def build_bot_help_message(*, topic: str | None = None) -> str:
    """Legacy full catalog help (no permission filter). Prefer build_help_message."""
    from assistant_platform.capabilities.catalog import (
        CAPABILITY_OPERATIONS,
        OWNER_EXTRA_KEYS,
        SELF_SERVICE_KEYS,
    )
    from assistant_platform.conversation.help import build_help_message_from_keys

    keys = list(dict.fromkeys(SELF_SERVICE_KEYS + OWNER_EXTRA_KEYS + ["bot.help"]))
    defined = {op["capability_key"] for op in CAPABILITY_OPERATIONS}
    return build_help_message_from_keys(
        (k for k in keys if k in defined),
        topic=topic,
    )


def _looks_like_help(text: str) -> bool:
    from assistant_platform.conversation.help import is_help_request

    return is_help_request(text)


def _handle_quota_command(
    text: str,
    user_id: str,
    config,
    repo: Repository,
    *,
    display_name: str | None = None,
) -> str | None:
    if text not in ("额度", "我的额度"):
        return None

    member = _dingtalk_member(repo, user_id, display_name)
    arguments: dict = {}

    if config.capability_bridge.quota_self_read:
        from pulse.channels.capability_bridge import invoke_capability_local, invoke_via_assistant

        try:
            return invoke_via_assistant(
                config=config,
                team_id=repo.team_id,
                member_id=member.id,
                role=member.portal_role,
                capability_key="quota.self.read",
                arguments=arguments,
                confirmed=True,
            )
        except Exception:
            logger.exception(
                "Capability bridge failed for quota.self.read; falling back to local invoke"
            )
            return invoke_capability_local(
                repo.session,
                config=config,
                team_id=repo.team_id,
                member_id=member.id,
                capability_key="quota.self.read",
                arguments=arguments,
                confirmed=True,
            )

    from pulse.channels.capability_bridge import invoke_capability_local

    return invoke_capability_local(
        repo.session,
        config=config,
        team_id=repo.team_id,
        member_id=member.id,
        capability_key="quota.self.read",
        arguments=arguments,
        confirmed=True,
    )


def handle_bind_cursor_command(
    text: str, user_id: str, config, repo, *, display_name: str | None = None
) -> str | None:
    match = BIND_CURSOR_RE.match(text.strip())
    if not match:
        return None

    member = _dingtalk_member(repo, user_id, display_name)
    email = match.group("email")
    api_key = match.group("key").strip()
    arguments: dict[str, str] = {"api_key": api_key}
    if email:
        arguments["email"] = email

    if config.capability_bridge.cursor_key_bind:
        from pulse.channels.capability_bridge import invoke_via_assistant

        try:
            return invoke_via_assistant(
                config=config,
                team_id=repo.team_id,
                member_id=member.id,
                role=member.portal_role,
                capability_key="cursor.key.bind",
                arguments=arguments,
                confirmed=True,
            )
        except Exception:
            logger.exception(
                "Capability bridge failed for cursor.key.bind; falling back to legacy bind"
            )

    from pulse.tool_center.cursor_bind import resolve_bind_cursor_account
    from pulse.tool_center.repository import ToolCenterRepository

    tool_repo = ToolCenterRepository(repo.session, repo.team_id)

    try:
        enc_key = _encryption_key(config)
    except ValueError as exc:
        return str(exc)

    from pulse.ingestion.credentials import AccountEmailMismatchError, CredentialService
    from pulse.ingestion.sync import CursorSyncService

    cred_service = CredentialService(repo.session, enc_key)
    account, note = resolve_bind_cursor_account(
        member=member,
        email=email,
        api_key=api_key,
        tool_repo=tool_repo,
        cred_service=cred_service,
        is_admin=_is_admin(user_id, set(config.admin.dingtalk_user_ids)),
    )
    if not account:
        return note

    if not _can_bind_account(config, member, account):
        return "仅账号主使用人或管理员可绑定 API Key。"

    try:
        cred = cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key=api_key,
            member_id=member.id,
        )
        sync_service = CursorSyncService(repo.session, enc_key)
        result = sync_service.sync_account(account.id, channel="dingtalk")
        repo.session.flush()
        prefix = f"{note}\n" if note else ""
        return (
            f"{prefix}"
            f"✅ 已绑定 Cursor 账号 {account.account_identifier}（{cred.key_hint}）\n"
            f"正在同步用量，写入 {result.event_count} 条事件。"
        )
    except AccountEmailMismatchError as exc:
        repo.session.rollback()
        return (
            f"⚠️ API Key 对应 Cursor 账号 {exc.key_email}，"
            f"与台账账号 {exc.ledger_email} 不一致。\n\n"
            f"若 Key 填错，请重新发送正确的 Key。"
        )
    except Exception as exc:
        repo.session.rollback()
        return f"绑定失败：{exc}"


def handle_unbind_cursor_command(
    text: str, user_id: str, config, repo, *, display_name: str | None = None
) -> str | None:
    match = UNBIND_CURSOR_RE.match(text.strip())
    if not match:
        return None

    member = _dingtalk_member(repo, user_id, display_name)
    email = match.group("email")

    from pulse.tool_center.account_pick import filter_cursor_accounts
    from pulse.tool_center.repository import ToolCenterRepository

    tool_repo = ToolCenterRepository(repo.session, repo.team_id)
    if email:
        needle = email.strip().lower()
        matches = [
            a
            for a in filter_cursor_accounts(tool_repo.list_active_accounts())
            if a.account_identifier.lower() == needle
        ]
        if not matches:
            return f"未找到 Cursor 账号 {email}"
        account = matches[0]
    else:
        cursor_accounts = filter_cursor_accounts(
            tool_repo.get_primary_accounts_for_member(member.id)
        )
        if not cursor_accounts:
            return "未找到您名下的 Cursor 账号。"
        if len(cursor_accounts) > 1:
            lines = ["您有多个 Cursor 账号，请指定邮箱，例如："]
            for acc in cursor_accounts:
                lines.append(f"解绑 cursor {acc.account_identifier}")
            return "\n".join(lines)
        account = cursor_accounts[0]

    if not _can_bind_account(config, member, account):
        return "仅账号主使用人或管理员可解绑 API Key。"

    try:
        enc_key = _encryption_key(config)
    except ValueError as exc:
        return str(exc)

    from pulse.ingestion.credentials import CredentialService

    cred_service = CredentialService(repo.session, enc_key)
    cred = cred_service.get_credential(account.id)
    if not cred or cred.status == "revoked":
        return f"账号 {account.account_identifier} 尚未绑定 API Key。"

    cred_service.revoke(account.id)
    repo.session.flush()
    return f"已解绑 Cursor 账号 {account.account_identifier} 的 API Key，自动同步已停止。"
