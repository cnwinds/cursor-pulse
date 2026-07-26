from __future__ import annotations

from pulse.config import AppConfig, DingTalkConfig, FeishuConfig, WebConfig
from pulse.web.auth_providers import list_auth_providers


def test_password_only_when_no_im_credentials():
    cfg = AppConfig(web=WebConfig(admin_password="secret"))
    providers = list_auth_providers(cfg)
    ids = [p["id"] for p in providers]
    assert ids == ["password"]
    assert providers[0]["enabled"] is True


def test_dingtalk_oauth_listed_when_credentials_present():
    cfg = AppConfig(
        web=WebConfig(admin_password="secret"),
        dingtalk=DingTalkConfig(app_key="k", app_secret="s"),
    )
    ids = [p["id"] for p in list_auth_providers(cfg)]
    assert "password" in ids
    assert "dingtalk_oauth" in ids
    assert "feishu_oauth" not in ids


def test_feishu_oauth_listed_when_credentials_present():
    cfg = AppConfig(
        web=WebConfig(admin_password=""),
        feishu=FeishuConfig(app_id="cli_x", app_secret="sec"),
    )
    providers = list_auth_providers(cfg)
    by_id = {p["id"]: p for p in providers}
    # Password login stays available for local users even without ADMIN_PASSWORD.
    assert by_id["password"]["enabled"] is True
    assert by_id["feishu_oauth"]["enabled"] is True
    assert by_id["feishu_oauth"]["login_url_path"] == "/api/auth/feishu/login-url"
