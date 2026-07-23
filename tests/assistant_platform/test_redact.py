from assistant_platform.secrets.redact import redact_text


def test_redact_cursor_api_key():
    text = "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz012345"
    redacted, refs = redact_text(text)
    assert "crsr_abcdefghijklmnopqrstuvwxyz012345" not in redacted
    assert "crsr_" in redacted or "CURSOR_KEY" in redacted
    assert len(refs) == 1
    assert refs[0]["kind"] == "cursor_api_key"
    assert refs[0]["secret"].startswith("crsr_")


def test_redact_leaves_normal_text():
    text = "帮我看下本月额度"
    redacted, refs = redact_text(text)
    assert redacted == text
    assert refs == []
