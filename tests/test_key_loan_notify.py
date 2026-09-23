"""Tests for key-loan lifecycle IM notifications."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pulse.config import (
    AdminConfig,
    AppConfig,
    BotPlatformConfig,
    ProxyAddress,
    ProxyConfig,
    TenantConfig,
)
from pulse.tool_center import key_loan_notify as notify
from pulse.tool_center.key_loan_ops import request_loan


def test_format_borrower_issued_includes_every_configured_proxy_address():
    addresses = [
        ProxyAddress(url="http://127.0.0.1:8317", display_name="本机代理"),
        ProxyAddress(url="http://192.168.11.39:8317", display_name="内网代理"),
    ]
    text = notify.format_borrower_issued(
        api_key="pka_testkey",
        loan_id="ae727a27-xxxx",
        loan_expires_on=None,
        delivery_mode="proxy_alias",
        addresses=addresses,
    )
    assert "本机代理 · Windows PowerShell" in text
    assert "本机代理 · Linux / macOS" in text
    assert "内网代理 · Windows PowerShell" in text
    assert "内网代理 · Linux / macOS" in text
    ps_idx = text.index("本机代理 · Windows PowerShell")
    bash_idx = text.index("本机代理 · Linux / macOS")
    inner_ps = text.index("内网代理 · Windows PowerShell")
    assert ps_idx < bash_idx < inner_ps
    assert 'set HTTPS_PROXY=http://127.0.0.1:8317&&' in text
    assert 'set HTTPS_PROXY=http://192.168.11.39:8317&&' in text
    assert 'HTTPS_PROXY="http://127.0.0.1:8317"' in text
    assert 'HTTPS_PROXY="http://192.168.11.39:8317"' in text
    assert "【Windows PowerShell】" not in text


def test_resolve_proxy_addresses_uses_team_settings_when_present(monkeypatch):
    team_addrs = [
        ProxyAddress(url="http://team-proxy:8317", display_name="团队代理"),
    ]
    monkeypatch.setattr(
        "pulse.settings.configured_proxy_addresses",
        lambda _session, _team_id: team_addrs,
    )
    monkeypatch.setattr(
        notify,
        "team_repository",
        lambda _session, _config: (SimpleNamespace(id="team-1"), None),
    )
    config = AppConfig(
        tenant=TenantConfig(slug="t", name="T"),
        proxy=ProxyConfig(public_url="http://fallback:8317"),
    )
    addrs = notify.resolve_proxy_addresses(MagicMock(), config)
    assert len(addrs) == 1
    assert addrs[0].url == "http://team-proxy:8317"


def test_format_borrower_issued_hides_lender_and_includes_shell_commands():
    text = notify.format_borrower_issued(
        api_key="pka_testkey",
        loan_id="abcdef12-xxxx",
        loan_expires_on="2026-08-01",
        warning="须配置 HTTPS_PROXY",
        proxy_url="http://proxy.example:8317",
        delivery_mode="proxy_alias",
        lender_mode="manual",
        routing_mode="pinned",
    )
    assert "分配方式：指定账号" in text
    assert "交付：代理别名" not in text
    assert "借出人" not in text
    assert "Alice" not in text
    assert "pka_testkey" in text
    assert "abcdef12" in text
    assert "http://proxy.example:8317 · Windows PowerShell" in text
    assert "http://proxy.example:8317 · Linux / macOS" in text
    assert (
        'cmd /c "set HTTPS_PROXY=http://proxy.example:8317&& '
        'set CURSOR_API_KEY=pka_testkey&& agent -k"'
    ) in text
    assert 'HTTPS_PROXY="http://proxy.example:8317" CURSOR_API_KEY="pka_testkey" agent -k' in text
    assert "export " not in text
    assert "$env:" not in text
    assert "须配置 HTTPS_PROXY" in text


def test_format_admin_issued_has_no_key_or_commands():
    text = notify.format_admin_issued(
        borrower_name="Borrower",
        loan_id="abcdef12-xxxx",
        loan_expires_on="2026-08-01",
        delivery_mode="proxy_alias",
    )
    assert "Borrower" in text
    assert "pka_" not in text
    assert "PowerShell" not in text
    assert "CURSOR_API_KEY" not in text


def test_format_borrower_reclaimed():
    text = notify.format_borrower_reclaimed(
        loan_id="abcdef12-xxxx",
        reason="expired",
        borrowed_cents=250,
    )
    assert "已回收" in text
    assert "到期自动回收" in text
    assert "$2.50" in text
    assert "借出人" not in text


def test_notify_loan_issued_sends_borrower_and_admin(monkeypatch):
    messenger = MagicMock()
    monkeypatch.setattr(notify, "outbound_messenger_or_none", lambda config: messenger)

    borrower = SimpleNamespace(
        id="m-borrower",
        display_name="Borrower",
        channel="dingtalk",
        channel_user_id="dt-borrower",
    )
    session = MagicMock()
    session.get.return_value = borrower

    config = AppConfig(
        tenant=TenantConfig(slug="t", name="T"),
        admin=AdminConfig(channel_user_ids=["dt-admin"]),
        bot=BotPlatformConfig(name="dingtalk"),
        proxy=ProxyConfig(public_url="http://proxy.example:8317"),
    )

    with patch(
        "pulse.identity.service.external_id_for",
        side_effect=lambda _s, member, channel: (
            "dt-borrower" if channel == "dingtalk" and member is borrower else None
        ),
    ):
        notify.notify_loan_issued(
            session,
            config,
            result={
                "loan_id": "loan-abcdef",
                "api_key": "pka_abc",
                "borrower_member_id": "m-borrower",
                "borrower_name": "Borrower",
                "loan_expires_on": "2026-08-01",
                "delivery_mode": "proxy_alias",
                "warning": "proxy warning",
            },
            skip_borrower=False,
        )

    assert messenger.send_oto_text.call_count == 2
    recipients = {c.args[0] for c in messenger.send_oto_text.call_args_list}
    assert recipients == {"dt-borrower", "dt-admin"}
    borrower_msg = next(
        c.args[1] for c in messenger.send_oto_text.call_args_list if c.args[0] == "dt-borrower"
    )
    admin_msg = next(
        c.args[1] for c in messenger.send_oto_text.call_args_list if c.args[0] == "dt-admin"
    )
    assert "pka_abc" in borrower_msg
    assert "PowerShell" in borrower_msg
    assert "借出人" not in borrower_msg
    assert "pka_abc" not in admin_msg
    assert "Borrower" in admin_msg


def test_notify_loan_issued_skip_borrower_only_admins(monkeypatch):
    messenger = MagicMock()
    monkeypatch.setattr(notify, "outbound_messenger_or_none", lambda config: messenger)
    config = AppConfig(
        tenant=TenantConfig(slug="t", name="T"),
        admin=AdminConfig(channel_user_ids=["dt-admin"]),
        bot=BotPlatformConfig(name="dingtalk"),
    )
    notify.notify_loan_issued(
        MagicMock(),
        config,
        result={
            "loan_id": "loan-abcdef",
            "api_key": "pka_abc",
            "borrower_name": "Borrower",
        },
        skip_borrower=True,
    )
    assert messenger.send_oto_text.call_count == 1
    assert messenger.send_oto_text.call_args.args[0] == "dt-admin"


def test_notify_noop_when_no_messenger(monkeypatch):
    monkeypatch.setattr(notify, "outbound_messenger_or_none", lambda config: None)
    notify.notify_loan_issued(
        MagicMock(),
        AppConfig(tenant=TenantConfig(slug="t", name="T")),
        result={"loan_id": "x", "api_key": "pka_x"},
    )


def test_request_loan_reply_hides_lender(monkeypatch):
    monkeypatch.setattr(
        "pulse.tool_center.key_loan_ops.request_loan_payload",
        lambda *args, **kwargs: {
            "ok": True,
            "api_key": "pka_secret",
            "loan_id": "abcd1234-xxxx",
            "loan_expires_on": "2026-08-01",
            "warning": "proxy warning",
            "delivery_mode": "proxy_alias",
            "lender_name": "SecretLender",
            "source_identifier": "lender@corp.com",
        },
    )
    config = AppConfig(
        tenant=TenantConfig(slug="t", name="T"),
        proxy=ProxyConfig(public_url="http://127.0.0.1:8317"),
    )
    msg = request_loan(MagicMock(), config, SimpleNamespace())
    assert "SecretLender" not in msg
    assert "lender@corp.com" not in msg
    assert "借出人" not in msg
    assert "pka_secret" in msg
    assert "PowerShell" in msg
    assert "Linux / macOS" in msg
