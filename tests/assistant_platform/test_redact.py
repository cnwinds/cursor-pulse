from assistant_platform.secrets.redact import redact_text


def test_redact_cursor_api_key():
    text = "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz012345"
    redacted, refs = redact_text(text)
    assert "crsr_abcdefghijklmnopqrstuvwxyz012345" not in redacted
    assert "crsr_" in redacted or "CURSOR_KEY" in redacted
    assert len(refs) == 1
    assert refs[0]["kind"] == "cursor_api_key"
    assert refs[0]["secret"].startswith("crsr_")


def test_redact_proxy_alias_key():
    key = "pka_" + ("a" * 32)
    text = f"Key：{key}\n请配置 HTTPS_PROXY"
    redacted, refs = redact_text(text)
    assert key not in redacted
    assert "PROXY_KEY" in redacted
    assert len(refs) == 1
    assert refs[0]["kind"] == "proxy_alias_key"
    assert refs[0]["secret"] == key


def test_redact_proxy_key():
    key = "pk_" + ("b" * 32)
    text = f"use {key} please"
    redacted, refs = redact_text(text)
    assert key not in redacted
    assert refs[0]["kind"] == "proxy_key"


def test_redact_leaves_normal_text():
    text = "帮我看下本月额度"
    redacted, refs = redact_text(text)
    assert redacted == text
    assert refs == []
