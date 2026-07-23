from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.cursor_key_bind import handle_cursor_key_bind
from pulse.capabilities.invoke import HANDLERS, invoke_capability
from pulse.config import AppConfig, CredentialConfig, TenantConfig
from pulse.ingestion.crypto import decrypt_secret
from pulse.storage.db import init_db
from pulse.storage.models import AiAccountCredential
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
FULL_API_KEY = "crsr_test_key_1234567890"


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


@pytest.fixture
def bind_env(session):
    team, repo = make_team_repo(session)
    actor = repo.add_member("actor-user", "Actor")
    other = repo.add_member("other-user", "Other")
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_accounts = [
        a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"
    ]
    actor_account = cursor_accounts[0]
    other_account = cursor_accounts[1]
    tool_repo.update_account(actor_account.id, primary_member_id=actor.id)
    tool_repo.update_account(other_account.id, primary_member_id=other.id)
    repo.commit()

    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    return {
        "team": team,
        "actor": actor,
        "other": other,
        "actor_account": actor_account,
        "other_account": other_account,
        "config": config,
    }


def _request(
    *,
    team_id: str,
    actor_member_id: str,
    arguments: dict | None = None,
    confirmed_by: str | None = "confirmer",
) -> CapabilityInvokeRequest:
    if arguments is None:
        arguments = {"api_key": FULL_API_KEY}
    return CapabilityInvokeRequest(
        invocation_id="inv-bind-1",
        idempotency_key="idem-bind-1",
        team_id=team_id,
        actor_member_id=actor_member_id,
        capability_key="cursor.key.bind",
        capability_version="1",
        arguments=arguments,
        confirmed_by=confirmed_by,
    )


def test_handler_registered():
    assert ("cursor.key.bind", "1") in HANDLERS


def test_confirmation_required_without_confirmed_by(session, bind_env):
    request = _request(
        team_id=bind_env["team"].id,
        actor_member_id=bind_env["actor"].id,
        confirmed_by=None,
    )
    result = handle_cursor_key_bind(
        session, request=request, config=bind_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "confirmation_required"


def test_missing_api_key_invalid_arguments(session, bind_env):
    request = _request(
        team_id=bind_env["team"].id,
        actor_member_id=bind_env["actor"].id,
        arguments={},
    )
    result = handle_cursor_key_bind(
        session, request=request, config=bind_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_arguments"


@patch("pulse.capabilities.handlers.cursor_key_bind.resolve_bind_cursor_account")
def test_forbidden_other_member_account_when_not_admin(
    mock_resolve, session, bind_env
):
    mock_resolve.return_value = (bind_env["other_account"], None)
    request = _request(
        team_id=bind_env["team"].id,
        actor_member_id=bind_env["actor"].id,
        arguments={
            "api_key": FULL_API_KEY,
            "email": bind_env["other_account"].account_identifier,
        },
    )
    result = handle_cursor_key_bind(
        session, request=request, config=bind_env["config"], op={}
    )

    assert result.status == "failed"
    assert result.error_code == "forbidden"


@patch("pulse.capabilities.handlers.cursor_key_bind.CursorSyncService")
@patch("pulse.ingestion.credentials.CursorApiClient")
def test_bind_success_encrypts_credential_and_masks_key_in_message(
    mock_client_cls, mock_sync_cls, session, bind_env
):
    mock_client = MagicMock()
    mock_cursor_key_exchange(
        mock_client, email=bind_env["actor_account"].account_identifier
    )
    mock_client_cls.return_value = mock_client
    mock_sync_cls.return_value.sync_account.return_value = MagicMock(event_count=2)

    request = _request(
        team_id=bind_env["team"].id,
        actor_member_id=bind_env["actor"].id,
    )
    result = handle_cursor_key_bind(
        session, request=request, config=bind_env["config"], op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    assert result.result.get("sync_ok") is True
    assert result.result.get("email") == bind_env["actor_account"].account_identifier
    assert FULL_API_KEY not in str(result.result)

    cred = session.scalar(
        select(AiAccountCredential).where(
            AiAccountCredential.account_id == bind_env["actor_account"].id
        )
    )
    assert cred is not None
    assert decrypt_secret(cred.encrypted_value, TEST_KEY) == FULL_API_KEY


@patch("pulse.capabilities.handlers.cursor_key_bind.CursorSyncService")
@patch("pulse.ingestion.credentials.CursorApiClient")
def test_bind_success_sync_fail_still_succeeded(
    mock_client_cls, mock_sync_cls, session, bind_env
):
    mock_client = MagicMock()
    mock_cursor_key_exchange(
        mock_client, email=bind_env["actor_account"].account_identifier
    )
    mock_client_cls.return_value = mock_client
    mock_sync_cls.return_value.sync_account.side_effect = RuntimeError("sync down")

    request = _request(
        team_id=bind_env["team"].id,
        actor_member_id=bind_env["actor"].id,
    )
    result = handle_cursor_key_bind(
        session, request=request, config=bind_env["config"], op={}
    )

    assert result.status == "succeeded"
    assert result.user_message == ""
    assert result.result.get("sync_ok") is False
    assert FULL_API_KEY not in str(result.result)


def test_invoke_capability_cursor_key_bind(session, bind_env):
    with (
        patch("pulse.ingestion.credentials.CursorApiClient") as mock_client_cls,
        patch(
            "pulse.capabilities.handlers.cursor_key_bind.CursorSyncService"
        ) as mock_sync_cls,
    ):
        mock_client = MagicMock()
        mock_cursor_key_exchange(
            mock_client, email=bind_env["actor_account"].account_identifier
        )
        mock_client_cls.return_value = mock_client
        mock_sync_cls.return_value.sync_account.return_value = MagicMock(
            event_count=1
        )

        request = _request(
            team_id=bind_env["team"].id,
            actor_member_id=bind_env["actor"].id,
        )
        result = invoke_capability(
            session, request=request, config=bind_env["config"]
        )

    assert result.status == "succeeded"
    assert result.result["account_id"] == bind_env["actor_account"].id
