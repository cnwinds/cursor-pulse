from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from pulse.config import AppConfig, CredentialConfig, TenantConfig
from pulse.ingestion.crypto import encrypt_secret, mask_api_key
from pulse.storage.db import init_db
from pulse.storage.models import AiAccountCredential
from pulse.tool_center.cursor_bind import resolve_bind_cursor_account
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def bind_ctx():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    member = repo.add_member("dt1", "Tester")
    tool_repo = ToolCenterRepository(session, team.id)
    cursor_accounts = [
        a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"
    ]
    bound = cursor_accounts[0]
    unbound = cursor_accounts[1] if len(cursor_accounts) > 1 else None
    tool_repo.update_account(bound.id, primary_member_id=member.id)
    if unbound:
        tool_repo.update_account(
            unbound.id,
            primary_member_id=member.id,
            account_identifier="",
        )
        session.add(
            AiAccountCredential(
                account_id=bound.id,
                vendor_id=bound.vendor_id,
                credential_type="cursor_api_key",
                encrypted_value=encrypt_secret("crsr_existing_key_1234567890", TEST_KEY),
                key_hint=mask_api_key("crsr_existing_key_1234567890"),
                key_role="primary",
                status="active",
                bound_by_member_id=member.id,
            )
        )
    session.commit()
    yield {
        "session": session,
        "repo": repo,
        "member": member,
        "tool_repo": tool_repo,
        "bound": bound,
        "unbound": unbound,
    }
    session.close()


def _mock_cred_service(*, bound_id: str, key_email: str | None = None) -> MagicMock:
    cred_service = MagicMock()

    def _primary(account_id: str):
        if account_id == bound_id:
            return MagicMock(encrypted_value="enc")
        return None

    cred_service.get_primary_credential.side_effect = _primary
    mock_cursor_key_exchange(cred_service.cursor_client, email=key_email)
    return cred_service


def test_wrong_email_falls_back_to_single_unbound(bind_ctx):
    ctx = bind_ctx
    if not ctx["unbound"]:
        pytest.skip("need two cursor accounts in catalog")

    cred_service = _mock_cred_service(bound_id=ctx["bound"].id, key_email=None)
    cred_service.cursor_client.exchange_user_api_key_response.side_effect = Exception(
        "offline"
    )

    account, note = resolve_bind_cursor_account(
        member=ctx["member"],
        email="wrong@gmail.com",
        api_key="crsr_test",
        tool_repo=ctx["tool_repo"],
        cred_service=cred_service,
        is_admin=False,
    )

    assert account is not None
    assert account.id == ctx["unbound"].id
    assert "尚未绑 Key" in (note or "")


def test_wrong_email_matches_key_email(bind_ctx):
    ctx = bind_ctx
    cred_service = _mock_cred_service(
        bound_id=ctx["bound"].id,
        key_email=ctx["bound"].account_identifier,
    )

    account, note = resolve_bind_cursor_account(
        member=ctx["member"],
        email="wrong@gmail.com",
        api_key="crsr_test",
        tool_repo=ctx["tool_repo"],
        cred_service=cred_service,
        is_admin=False,
    )

    assert account is not None
    assert account.id == ctx["bound"].id
    assert "按 Key 对应邮箱" in (note or "")


@patch("pulse.ingestion.sync.CursorSyncService")
@patch("pulse.ingestion.credentials.CredentialService")
@patch("pulse.tool_center.cursor_bind.resolve_bind_cursor_account")
def test_bind_command_shows_note_from_resolver(
    mock_resolve, mock_cred_cls, mock_sync_cls, bind_ctx
):
    ctx = bind_ctx
    if not ctx["unbound"]:
        pytest.skip("need two cursor accounts in catalog")

    from pulse.channels.commands import handle_bind_cursor_command

    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    note = "台账中未找到 wrong@gmail.com，已绑定到你尚未绑 Key 的账号（未填邮箱）。"
    mock_resolve.return_value = (ctx["unbound"], note)
    cred = MagicMock()
    cred.key_hint = "crsr_...7890"
    mock_cred_cls.return_value.bind_cursor_api_key.return_value = cred
    mock_sync_cls.return_value.sync_account.return_value = MagicMock(event_count=1)

    reply = handle_bind_cursor_command(
        "绑定 cursor wrong@gmail.com crsr_test_key_1234567890",
        ctx["member"].dingtalk_user_id,
        config,
        ctx["repo"],
    )

    assert reply is not None
    assert note in reply
    assert "已绑定" in reply
