"""Member multi-channel identity helpers."""

from pulse.identity.service import (
    ensure_identity,
    link_identity,
    list_identities,
    merge_members,
    refresh_member_primary_cache,
    resolve_member,
    set_member_password,
)

__all__ = [
    "ensure_identity",
    "link_identity",
    "list_identities",
    "merge_members",
    "refresh_member_primary_cache",
    "resolve_member",
    "set_member_password",
]
