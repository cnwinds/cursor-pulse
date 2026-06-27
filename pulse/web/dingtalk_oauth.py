from __future__ import annotations

import secrets
from urllib.parse import quote

import httpx

from pulse.config import AppConfig


class DingTalkOAuthError(RuntimeError):
    pass


def build_login_url(config: AppConfig, *, state: str | None = None) -> tuple[str, str]:
    if not config.dingtalk.app_key:
        raise DingTalkOAuthError("未配置 DINGTALK_APP_KEY")
    state = state or secrets.token_urlsafe(24)
    redirect = quote(config.web.dingtalk_oauth_redirect_uri, safe="")
    url = (
        "https://login.dingtalk.com/oauth2/auth"
        f"?client_id={config.dingtalk.app_key}"
        f"&response_type=code"
        f"&scope=openid"
        f"&state={state}"
        f"&redirect_uri={redirect}"
        f"&prompt=consent"
    )
    return url, state


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

    userid = me.get("userId") or me.get("unionId") or me.get("openId") or me.get("nick")
    if not userid:
        raise DingTalkOAuthError(f"无法解析钉钉用户 ID: {me}")
    name = me.get("nick") or me.get("name") or str(userid)
    return str(userid), name
