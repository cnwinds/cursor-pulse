from __future__ import annotations

from types import SimpleNamespace

from pulse.authz.actor import can_manage_guide_image, is_channel_admin
from pulse.config import AdminConfig, AppConfig, StorageConfig, TenantConfig


def _member(*, portal_role: str | None, dingtalk_user_id: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(portal_role=portal_role, dingtalk_user_id=dingtalk_user_id)


def _config(*, dingtalk_user_ids: list[str] | None = None) -> AppConfig:
    return AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        storage=StorageConfig(),
        admin=AdminConfig(dingtalk_user_ids=dingtalk_user_ids or []),
    )


def test_owner_can_manage_guide_image():
    assert can_manage_guide_image(_member(portal_role="owner"), _config()) is True


def test_operator_can_manage_guide_image():
    assert can_manage_guide_image(_member(portal_role="operator"), _config()) is True


def test_ai_member_cannot_manage_guide_image():
    assert can_manage_guide_image(_member(portal_role="ai_member"), _config()) is False


def test_owner_is_channel_admin():
    assert is_channel_admin(_member(portal_role="owner"), _config()) is True


def test_ai_member_is_not_channel_admin_without_dingtalk_id():
    assert is_channel_admin(_member(portal_role="ai_member"), _config()) is False


def test_dingtalk_admin_can_manage_guide_image_even_if_ai_member():
    member = _member(portal_role="ai_member", dingtalk_user_id="dingtalk-admin")
    config = _config(dingtalk_user_ids=["dingtalk-admin"])
    assert can_manage_guide_image(member, config) is True
