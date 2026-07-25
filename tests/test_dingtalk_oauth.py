from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pulse.config import AppConfig, DingTalkConfig, WebConfig
from pulse.web.dingtalk_oauth import (
    DingTalkOAuthError,
    build_login_url,
    looks_like_open_id,
    resolve_enterprise_userid,
    resolve_oauth_redirect_uri,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("q1kd0KjUKjamrEbcOqeGjQiEiE", True),
        ("1584929783723323", False),
        ("zhangsan", True),
    ],
)
def test_looks_like_open_id(value: str, expected: bool):
    assert looks_like_open_id(value) is expected


def test_resolve_enterprise_userid_prefers_user_id():
    config = AppConfig(dingtalk=DingTalkConfig(app_key="k", app_secret="s"))
    assert resolve_enterprise_userid(config, {"userId": "1584929783723323"}) == "1584929783723323"


def test_resolve_enterprise_userid_uses_unionid_lookup():
    config = AppConfig(dingtalk=DingTalkConfig(app_key="k", app_secret="s"))
    client = MagicMock()
    client.get_userid_by_unionid.return_value = "1584929783723323"

    with patch("pulse.channels.dingtalk.messenger.DingTalkMessenger") as messenger_cls, patch(
        "pulse.integrations.dingtalk_directory.DingTalkDirectoryClient",
        return_value=client,
    ):
        messenger_cls.return_value.get_access_token.return_value = "token"
        userid = resolve_enterprise_userid(
            config,
            {"unionId": "union-abc", "openId": "q1kd0KjUKjamrEbcOqeGjQiEiE"},
        )

    assert userid == "1584929783723323"
    client.get_userid_by_unionid.assert_called_once_with("union-abc")


def test_resolve_enterprise_userid_rejects_openid_only():
    config = AppConfig(dingtalk=DingTalkConfig(app_key="k", app_secret="s"))
    with pytest.raises(DingTalkOAuthError, match="openId"):
        resolve_enterprise_userid(config, {"openId": "q1kd0KjUKjamrEbcOqeGjQiEiE", "nick": "熊波"})


def test_resolve_oauth_redirect_uri_uses_request_when_cors_allows():
    config = AppConfig(
        dingtalk=DingTalkConfig(app_key="k", app_secret="s"),
        web=WebConfig(
            dingtalk_oauth_redirect_uri="http://192.168.11.39:8080/admin/login/callback",
            cors_origins=[
                "http://192.168.11.39:8080",
                "http://192.168.11.39:5173",
            ],
        ),
    )
    assert (
        resolve_oauth_redirect_uri(
            config, "http://192.168.11.39:5173/login/callback"
        )
        == "http://192.168.11.39:5173/login/callback"
    )


def test_resolve_oauth_redirect_uri_rejects_unknown_origin():
    config = AppConfig(
        dingtalk=DingTalkConfig(app_key="k"),
        web=WebConfig(
            dingtalk_oauth_redirect_uri="http://192.168.11.39:8080/admin/login/callback",
            cors_origins=["http://192.168.11.39:8080"],
        ),
    )
    with pytest.raises(DingTalkOAuthError, match="不在允许列表"):
        resolve_oauth_redirect_uri(config, "http://evil.example/login/callback")


def test_build_login_url_embeds_resolved_redirect():
    config = AppConfig(
        dingtalk=DingTalkConfig(app_key="appk"),
        web=WebConfig(
            dingtalk_oauth_redirect_uri="http://192.168.11.39:8080/admin/login/callback",
            cors_origins=["http://192.168.11.39:5173"],
        ),
    )
    url, _state = build_login_url(
        config, redirect_uri="http://192.168.11.39:5173/login/callback"
    )
    assert "redirect_uri=http%3A%2F%2F192.168.11.39%3A5173%2Flogin%2Fcallback" in url
