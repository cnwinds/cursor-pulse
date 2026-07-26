"""Portal auth providers: password (local) + optional IM OAuth.

Availability is derived from config so the Login UI can hide unavailable
options (e.g. no DingTalk/Feishu credentials → password-only).
"""

from __future__ import annotations

from typing import Any

from pulse.config import AppConfig


def list_auth_providers(config: AppConfig) -> list[dict[str, Any]]:
    """Return enabled login providers for the portal.

    Always includes ``password`` when ``ADMIN_PASSWORD`` is set (or even when
    unset — the login endpoint will 503 with a clear message). IM OAuth
    providers appear only when their app credentials are configured.
    """
    providers: list[dict[str, Any]] = [
        {
            "id": "password",
            "label": "本地密码",
            "kind": "password",
            # Local users may exist without ADMIN_PASSWORD; login validates per-account.
            "enabled": True,
        }
    ]
    if (config.dingtalk.app_key or "").strip() and (config.dingtalk.app_secret or "").strip():
        providers.append(
            {
                "id": "dingtalk_oauth",
                "label": "钉钉扫码",
                "kind": "oauth",
                "enabled": True,
                "login_url_path": "/api/auth/dingtalk/login-url",
                "callback_path": "/api/auth/dingtalk/callback",
            }
        )
    if (config.feishu.app_id or "").strip() and (config.feishu.app_secret or "").strip():
        providers.append(
            {
                "id": "feishu_oauth",
                "label": "飞书扫码",
                "kind": "oauth",
                "enabled": True,
                "login_url_path": "/api/auth/feishu/login-url",
                "callback_path": "/api/auth/feishu/callback",
            }
        )
    return providers
