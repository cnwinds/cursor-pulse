from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PasswordLoginBody(BaseModel):
    username: str = "admin"
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


class PortalApproveBody(BaseModel):
    portal_role: str
    portal_permissions: list[str] | None = None


class BindCredentialBody(BaseModel):
    api_key: str
