from __future__ import annotations

def _msg(result):
    data = result.result or {}
    return result.user_message or data.get("text") or data.get("answer") or ""


import base64
from pathlib import Path

import pytest

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.guide_image_update import handle_guide_image_update
from pulse.capabilities.invoke import HANDLERS
from pulse.config import AppConfig, AdminConfig, StorageConfig, TenantConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo

# Minimal valid PNG (1x1)
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


@pytest.fixture
def guide_env(session, tmp_path):
    team, repo = make_team_repo(session)
    owner = repo.add_member("owner-user", "Owner")
    owner.portal_role = "owner"
    member = repo.add_member("member-user", "Member")
    member.portal_role = "ai_member"
    repo.commit()

    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        storage=StorageConfig(raw_files_dir=str(tmp_path)),
        admin=AdminConfig(dingtalk_user_ids=[]),
    )
    return {
        "team": team,
        "owner": owner,
        "member": member,
        "config": config,
        "raw_files_dir": tmp_path,
    }


def _request(
    *,
    team_id: str,
    actor_member_id: str,
    arguments: dict | None = None,
    confirmed_by: str | None = "confirmer",
) -> CapabilityInvokeRequest:
    if arguments is None:
        arguments = {"image_base64": base64.standard_b64encode(PNG_BYTES).decode("ascii")}
    return CapabilityInvokeRequest(
        invocation_id="inv-guide-1",
        idempotency_key="idem-guide-1",
        team_id=team_id,
        actor_member_id=actor_member_id,
        capability_key="guide_image.update",
        capability_version="1",
        arguments=arguments,
        confirmed_by=confirmed_by,
    )


def test_handler_registered():
    assert ("guide_image.update", "1") in HANDLERS


def test_confirmation_required_without_confirmed_by(session, guide_env):
    request = _request(
        team_id=guide_env["team"].id,
        actor_member_id=guide_env["owner"].id,
        confirmed_by=None,
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "confirmation_required"


def test_forbidden_for_normal_member(session, guide_env):
    request = _request(
        team_id=guide_env["team"].id,
        actor_member_id=guide_env["member"].id,
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "forbidden"


def test_invalid_arguments_for_empty_image(session, guide_env):
    request = _request(
        team_id=guide_env["team"].id,
        actor_member_id=guide_env["owner"].id,
        arguments={"image_base64": base64.standard_b64encode(b"").decode("ascii")},
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_arguments"


def test_success_for_owner_with_image_base64(session, guide_env):
    request = _request(
        team_id=guide_env["team"].id,
        actor_member_id=guide_env["owner"].id,
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    assert (result.result or {}).get("updated") is True
    saved = guide_env["raw_files_dir"] / "assets" / "cursor_bind_key_guide.png"
    assert saved.is_file()
    assert saved.read_bytes() == PNG_BYTES


def test_success_for_owner_with_image_path(session, guide_env, tmp_path):
    source = tmp_path / "upload.png"
    source.write_bytes(PNG_BYTES)
    request = _request(
        team_id=guide_env["team"].id,
        actor_member_id=guide_env["owner"].id,
        arguments={"image_path": str(source)},
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "succeeded"
    saved = guide_env["raw_files_dir"] / "assets" / "cursor_bind_key_guide.png"
    assert saved.read_bytes() == PNG_BYTES


def test_success_for_operator_with_image_base64(session, guide_env):
    team, repo = make_team_repo(session)
    operator = repo.add_member("operator-user", "Operator")
    operator.portal_role = "operator"
    repo.commit()

    request = _request(
        team_id=team.id,
        actor_member_id=operator.id,
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    assert (result.result or {}).get("updated") is True
    saved = guide_env["raw_files_dir"] / "assets" / "cursor_bind_key_guide.png"
    assert saved.is_file()
    assert saved.read_bytes() == PNG_BYTES


def test_success_for_dingtalk_admin_without_portal_role(session, guide_env):
    team, repo = make_team_repo(session)
    admin_member = repo.add_member("dingtalk-admin-user", "DingTalk Admin")
    admin_member.portal_role = "ai_member"
    repo.commit()

    config = guide_env["config"].model_copy(
        update={"admin": AdminConfig(dingtalk_user_ids=["dingtalk-admin-user"])}
    )
    request = _request(
        team_id=team.id,
        actor_member_id=admin_member.id,
    )
    result = handle_guide_image_update(
        session, request=request, config=config, op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    assert (result.result or {}).get("updated") is True
    saved = guide_env["raw_files_dir"] / "assets" / "cursor_bind_key_guide.png"
    assert saved.is_file()
    assert saved.read_bytes() == PNG_BYTES


def test_invalid_arguments_for_non_image_magic_bytes(session, guide_env):
    invalid_bytes = b"not-an-image-xxx"
    request = _request(
        team_id=guide_env["team"].id,
        actor_member_id=guide_env["owner"].id,
        arguments={
            "image_base64": base64.standard_b64encode(invalid_bytes).decode("ascii")
        },
    )
    result = handle_guide_image_update(
        session, request=request, config=guide_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_arguments"

