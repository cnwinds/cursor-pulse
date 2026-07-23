from pulse.channels.admin_gate import is_dingtalk_admin


def test_empty_admin_list_means_nobody_is_admin():
    assert is_dingtalk_admin("u1", []) is False
    assert is_dingtalk_admin("u1", set()) is False


def test_listed_user_is_admin():
    assert is_dingtalk_admin("admin1", ["admin1", "admin2"]) is True
    assert is_dingtalk_admin("other", ["admin1"]) is False
