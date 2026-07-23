from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pulse.config import AppConfig, DingTalkConfig
from pulse.web.dingtalk_oauth import (
    DingTalkOAuthError,
    looks_like_open_id,
    resolve_enterprise_userid,
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
