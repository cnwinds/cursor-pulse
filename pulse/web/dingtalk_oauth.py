from __future__ import annotations

import secrets
from urllib.parse import quote

import httpx

from pulse.config import AppConfig


class DingTalkOAuthError(RuntimeError):
    pass


_OAUTH_SCOPE = "openid"
_CONTACT_READ_HINT = (
    "请在钉钉开放平台为应用申请「通讯录个人信息读」(Contact.User.Read) 权限并重新发布应用后重试。"
)


def build_login_url(config: AppConfig, *, state: str | None = None) -> tuple[str, str]:
    if not config.dingtalk.app_key:
        raise DingTalkOAuthError("未配置 DINGTALK_APP_KEY")
    state = state or secrets.token_urlsafe(24)
    redirect = quote(config.web.dingtalk_oauth_redirect_uri, safe="")
    url = (
        "https://login.dingtalk.com/oauth2/auth"
        f"?client_id={config.dingtalk.app_key}"
        f"&response_type=code"
        f"&scope={quote(_OAUTH_SCOPE, safe='')}"
        f"&state={state}"
        f"&redirect_uri={redirect}"
        f"&prompt=consent"
    )
    return url, state


def _pick_field(data: dict, *keys: str) -> str | None:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value:
            return str(value)
    return None


def looks_like_open_id(value: str) -> bool:
    """OAuth openId 含字母；企业通讯录 userid 在本项目中为纯数字字符串。"""
    return any(char.isalpha() for char in value)


def resolve_enterprise_userid(config: AppConfig, me: dict) -> str:
    """将 OAuth 用户信息解析为与通讯录同步一致的企业 userid。"""
    user_id = _pick_field(me, "userId", "userid")
    if user_id and not looks_like_open_id(user_id):
        return user_id

    union_id = _pick_field(me, "unionId", "unionid")
    if union_id:
        from pulse.bot.dingtalk.messenger import DingTalkMessenger
        from pulse.integrations.dingtalk_directory import DingTalkDirectoryClient

        client = DingTalkDirectoryClient(DingTalkMessenger(config).get_access_token)
        try:
            return client.get_userid_by_unionid(union_id)
        except RuntimeError as exc:
            raise DingTalkOAuthError(f"根据 unionId 解析企业 userid 失败: {exc}") from exc

    open_id = _pick_field(me, "openId", "openid")
    if open_id:
        raise DingTalkOAuthError(
            "钉钉 OAuth 仅返回 openId，无法与通讯录 userid 对齐。"
            f"{_CONTACT_READ_HINT}"
        )

    raise DingTalkOAuthError(f"无法解析钉钉企业 userid: {me}")


def exchange_code_for_userid(config: AppConfig, code: str) -> tuple[str, str]:
    if not config.dingtalk.app_key or not config.dingtalk.app_secret:
        raise DingTalkOAuthError("未配置钉钉应用凭证")

    with httpx.Client(timeout=30.0) as client:
        token_resp = client.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": config.dingtalk.app_key,
                "clientSecret": config.dingtalk.app_secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        if token_resp.status_code >= 400:
            raise DingTalkOAuthError(f"换取 userAccessToken 失败: {token_resp.text}")
        token_data = token_resp.json()
        access_token = token_data.get("accessToken")
        if not access_token:
            raise DingTalkOAuthError(f"钉钉未返回 accessToken: {token_data}")

        me_resp = client.get(
            "https://api.dingtalk.com/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        if me_resp.status_code >= 400:
            raise DingTalkOAuthError(f"获取用户信息失败: {me_resp.text}")
        me = me_resp.json()

    userid = resolve_enterprise_userid(config, me)
    name = _pick_field(me, "nick", "name") or userid
    return userid, name
