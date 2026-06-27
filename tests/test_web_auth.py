import pytest

from pulse.config import AppConfig, WebConfig
from pulse.web.permissions import has_permission, resolve_permissions
from pulse.storage.models import Member


def test_owner_has_all_permissions():
    member = Member(
        team_id="t1",
        dingtalk_user_id="u1",
        display_name="Owner",
        status="active",
        portal_role="owner",
    )
    perms = resolve_permissions(member)
    assert "settings:write" in perms
    assert has_permission(member, "admin:users")


def test_auditor_read_only_write_denied():
    member = Member(
        team_id="t1",
        dingtalk_user_id="u2",
        display_name="Auditor",
        status="active",
        portal_role="auditor",
    )
    assert has_permission(member, "metrics:read")
    assert not has_permission(member, "settings:write")


def test_jwt_roundtrip():
    jwt = pytest.importorskip("jwt")
    from pulse.config import AppConfig, WebConfig
    from pulse.web.auth_tokens import create_access_token, decode_access_token

    config = AppConfig(web=WebConfig(jwt_secret="test-secret"))
    member = Member(
        team_id="t1",
        dingtalk_user_id="u1",
        display_name="X",
        status="active",
        portal_role="operator",
    )
    member.id = "mem-1"
    token = create_access_token(config, member)
    payload = decode_access_token(config, token)
    assert payload["sub"] == "mem-1"
    assert "metrics:read" in payload["permissions"]
