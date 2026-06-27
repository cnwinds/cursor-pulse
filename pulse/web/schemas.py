from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PasswordLoginBody(BaseModel):
    dingtalk_user_id: str
    password: str


class DingTalkCallbackBody(BaseModel):
    code: str


class ChatBody(BaseModel):
    message: str


class SettingsPatchBody(BaseModel):
    data: dict[str, Any]


class PrincipleCreateBody(BaseModel):
    rule: str
    tier: str = "learned"
    origin: str | None = None


class PortalGrantBody(BaseModel):
    portal_role: str | None = None
    portal_permissions: list[str] | None = None
    display_name: str | None = None
