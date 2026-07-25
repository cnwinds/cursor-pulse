from __future__ import annotations

from pulse.authz.actor import is_channel_admin
from pulse.channels.admin_gate import is_channel_admin as _is_admin
from pulse.storage.repository import Repository


def encryption_key(config) -> str:
    key = (config.credentials.encryption_key or "").strip()
    if not key:
        raise ValueError("系统未配置凭证加密密钥，请联系管理员")
    return key


def can_bind_account(config, member, account) -> bool:
    if is_channel_admin(member, config):
        return True
    return account.primary_member_id == member.id


def channel_admin(
    user_id: str, config, repo: Repository, *, channel: str | None = None
) -> bool:
    member = repo.get_member_by_channel_user_id(user_id, channel=channel)
    if member is not None:
        return is_channel_admin(member, config)
    return _is_admin(user_id, set(config.admin.channel_user_ids))


def channel_member(
    repo,
    user_id: str,
    display_name: str | None = None,
    *,
    channel: str = "dingtalk",
):
    return repo.get_or_create_member(
        user_id, display_name or user_id, channel=channel
    )


# Backward-compatible alias (defaults to dingtalk channel).
def dingtalk_member(repo, user_id: str, display_name: str | None = None, *, channel: str = "dingtalk"):
    return channel_member(repo, user_id, display_name, channel=channel)
