from __future__ import annotations

from pulse.ingestion.credentials import CredentialService
from pulse.storage.models import AiAccount, Member
from pulse.tool_center.account_pick import filter_cursor_accounts
from pulse.tool_center.repository import ToolCenterRepository


def account_needs_bind(cred_service: CredentialService, account_id: str) -> bool:
    cred = cred_service.get_primary_credential(account_id)
    return not (cred and cred.encrypted_value)


def _account_label(account: AiAccount) -> str:
    text = (account.account_identifier or "").strip()
    return text or "未填邮箱"


def _match_by_email(accounts: list[AiAccount], email: str) -> AiAccount | None:
    needle = email.strip().lower()
    for account in accounts:
        identifier = (account.account_identifier or "").strip().lower()
        if identifier and identifier == needle:
            return account
    return None


def _unbound_accounts(
    cred_service: CredentialService, accounts: list[AiAccount]
) -> list[AiAccount]:
    return [account for account in accounts if account_needs_bind(cred_service, account.id)]


def _multi_account_bind_hint(
    accounts: list[AiAccount], cred_service: CredentialService
) -> str:
    lines = ["您有多个 Cursor 账号，请指定要绑定的账号，例如："]
    for account in accounts:
        status = "未绑 Key" if account_needs_bind(cred_service, account.id) else "已绑"
        lines.append(f"绑定 cursor {_account_label(account)} crsr_...（{status}）")
    lines.append("若仅有一个未绑账号，也可直接：绑定 cursor key crsr_...")
    return "\n".join(lines)


def _resolve_key_email(cred_service: CredentialService, api_key: str) -> str | None:
    try:
        exchange = cred_service.cursor_client.exchange_user_api_key_response(api_key)
        return cred_service.cursor_client.resolve_api_key_account_email(
            api_key, exchange=exchange
        )
    except Exception:
        return None


def resolve_bind_cursor_account(
    *,
    member: Member,
    email: str | None,
    api_key: str,
    tool_repo: ToolCenterRepository,
    cred_service: CredentialService,
    is_admin: bool,
) -> tuple[AiAccount | None, str | None]:
    """返回 (account, note)。account 为 None 时 note 为错误提示。"""
    own_accounts = filter_cursor_accounts(
        tool_repo.get_primary_accounts_for_member(member.id)
    )
    search_pool = (
        filter_cursor_accounts(tool_repo.list_active_accounts())
        if is_admin
        else own_accounts
    )

    if email:
        matched = _match_by_email(search_pool, email)
        if matched:
            if not is_admin and matched not in own_accounts:
                return None, "该账号不在您名下，无法绑定。"
            return matched, None

    key_email = _resolve_key_email(cred_service, api_key)
    key_pool = search_pool if is_admin else own_accounts
    if key_email:
        key_matched = _match_by_email(key_pool, key_email)
        if key_matched:
            if not is_admin and key_matched not in own_accounts:
                return None, "该账号不在您名下，无法绑定。"
            if email:
                return (
                    key_matched,
                    f"台账中未找到 {email}，已按 Key 对应邮箱 {key_email} 匹配。",
                )
            return key_matched, f"已按 Key 对应邮箱 {key_email} 匹配账号。"

    unbound_own = _unbound_accounts(cred_service, own_accounts)
    if email and not _match_by_email(search_pool, email):
        if not is_admin:
            if len(unbound_own) == 1:
                account = unbound_own[0]
                return (
                    account,
                    f"台账中未找到 {email}，已绑定到你尚未绑 Key 的账号（{_account_label(account)}）。",
                )
            if len(unbound_own) > 1:
                lines = [
                    f"未找到 Cursor 账号 {email}，你名下有 {len(unbound_own)} 个账号未绑 Key："
                ]
                for account in unbound_own:
                    lines.append(f"· {_account_label(account)}")
                lines.append("请指定其中一个邮箱后重试。")
                return None, "\n".join(lines)
        return None, f"未找到 Cursor 账号 {email}"

    if not own_accounts:
        return None, "未找到您名下的 Cursor 主使用人账号，请联系管理员配置台账。"

    if len(own_accounts) == 1:
        return own_accounts[0], None

    if len(unbound_own) == 1:
        account = unbound_own[0]
        return account, f"已绑定到你尚未绑 Key 的账号（{_account_label(account)}）。"

    return None, _multi_account_bind_hint(own_accounts, cred_service)
