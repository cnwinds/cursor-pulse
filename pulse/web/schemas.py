from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PasswordLoginBody(BaseModel):
    username: str = "admin"
    password: str


class DingTalkCallbackBody(BaseModel):
    code: str


class FeishuCallbackBody(BaseModel):
    code: str
    redirect_uri: str | None = None


class ChatBody(BaseModel):
    message: str


class SettingsPatchBody(BaseModel):
    data: dict[str, Any]


class PortalApproveBody(BaseModel):
    portal_role: str
    portal_permissions: list[str] | None = None


class PortalCreateUserBody(BaseModel):
    username: str
    display_name: str
    password: str
    portal_role: str = "operator"
    portal_permissions: list[str] | None = None


class PortalLinkIdentityBody(BaseModel):
    channel: str
    external_id: str
    merge: bool = False


class PortalSetPasswordBody(BaseModel):
    password: str
    username: str | None = None  # create web identity if member has none yet


class BindCredentialBody(BaseModel):
    api_key: str
