from __future__ import annotations


def resolve_subject_id(*, member_id: str | None, channel_user_id: str | None) -> str:
    mid = (member_id or "").strip()
    if mid:
        return mid
    return (channel_user_id or "").strip()
