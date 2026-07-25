from __future__ import annotations

import secrets
from urllib.parse import quote

from pulse.config import AppConfig
from pulse.http_clients import outbound_client
from pulse.web.dingtalk_oauth import allowed_oauth_redirect_uris, resolve_oauth_redirect_uri


class FeishuOAuthError(RuntimeError):
    pass


_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
# Minimal scopes for identity; expand in developer console as needed.
_OAUTH_SCOPE = "contact:user.base:readonly"


def build_login_url(
    config: AppConfig,
    *,
    state: str | None = None,
    redirect_uri: str | None = None,
) -> tuple[str, str]:
    if not config.feishu.app_id:
        raise FeishuOAuthError("未配置 FEISHU_APP_ID")
    state = state or secrets.token_urlsafe(24)
    try:
        resolved = resolve_oauth_redirect_uri(config, redirect_uri)
    except Exception as exc:
        # Re-raise as Feishu-specific for API layer consistency
        raise FeishuOAuthError(str(exc)) from exc
    redirect = quote(resolved, safe="")
    scope = quote(_OAUTH_SCOPE, safe="")
    url = (
        f"{_AUTHORIZE_URL}"
        f"?client_id={config.feishu.app_id}"
        f"&response_type=code"
        f"&redirect_uri={redirect}"
        f"&state={state}"
        f"&scope={scope}"
    )
    return url, state


def exchange_code_for_userid(
    config: AppConfig, code: str, *, redirect_uri: str | None = None
) -> tuple[str, str]:
    if not config.feishu.app_id or not config.feishu.app_secret:
        raise FeishuOAuthError("未配置飞书应用凭证")

    try:
        resolved = resolve_oauth_redirect_uri(config, redirect_uri)
    except Exception as exc:
        raise FeishuOAuthError(str(exc)) from exc

    with outbound_client(timeout=30.0) as client:
        token_resp = client.post(
            _TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": config.feishu.app_id,
                "client_secret": config.feishu.app_secret,
                "code": code,
                "redirect_uri": resolved,
            },
        )
        if token_resp.status_code >= 400:
            raise FeishuOAuthError(f"换取 user_access_token 失败: {token_resp.text}")
        token_data = token_resp.json()
        if token_data.get("code") not in (None, 0):
            raise FeishuOAuthError(
                f"换取 user_access_token 失败: {token_data.get('error_description') or token_data}"
            )
        access_token = token_data.get("access_token")
        if not access_token:
            raise FeishuOAuthError(f"飞书未返回 access_token: {token_data}")

        me_resp = client.get(
            _USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if me_resp.status_code >= 400:
            raise FeishuOAuthError(f"获取用户信息失败: {me_resp.text}")
        payload = me_resp.json()
        if payload.get("code") not in (None, 0):
            raise FeishuOAuthError(f"获取用户信息失败: {payload}")
        me = payload.get("data") or payload

    open_id = me.get("open_id") or me.get("user_id") or me.get("union_id")
    if not open_id:
        raise FeishuOAuthError(f"无法解析飞书用户标识: {me}")
    name = me.get("name") or me.get("en_name") or str(open_id)
    return str(open_id), str(name)


# Re-export allowlist helper for tests / callers
__all__ = [
    "FeishuOAuthError",
    "allowed_oauth_redirect_uris",
    "build_login_url",
    "exchange_code_for_userid",
]
