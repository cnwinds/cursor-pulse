from pulse.web.account_create_handlers import _coding_plan_account_identifier


def test_coding_plan_identifier_uses_mask_when_empty():
    assert _coding_plan_account_identifier("", "abcdefgh12345678") == "abcde...5678"


def test_coding_plan_identifier_keeps_user_label():
    assert _coding_plan_account_identifier("  me@x.com ", "ignored") == "me@x.com"
