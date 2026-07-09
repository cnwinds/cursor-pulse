from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from pulse.bot.commands import (
    BIND_CURSOR_RE,
    CURSOR_BIND_GUIDE,
    UNBIND_CURSOR_RE,
    handle_bind_cursor_command,
    handle_unbind_cursor_command,
    run_command,
)
from pulse.config import AppConfig, CredentialConfig, TenantConfig
from pulse.storage.db import init_db
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def test_bind_regex_matches():
    assert BIND_CURSOR_RE.match("绑定 cursor key crsr_abc123")
    assert BIND_CURSOR_RE.match("绑定 cursor user@test.com crsr_abc123")
    assert BIND_CURSOR_RE.match("绑定 CURSOR key crsr_abc123")


def test_unbind_regex_matches():
    assert UNBIND_CURSOR_RE.match("解绑 cursor")
    assert UNBIND_CURSOR_RE.match("解绑 cursor user@test.com")


@pytest.fixture
def bot_repo():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    member = repo.add_member("dt1", "Tester")
    tool_repo = ToolCenterRepository(session, team.id)
    cursor = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(cursor.id, primary_member_id=member.id)
    session.commit()
    yield repo, member, cursor
    session.close()


def test_bind_command_requires_encryption_key(bot_repo):
    repo, member, cursor = bot_repo
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=""),
    )
    reply = handle_bind_cursor_command(
        f"绑定 cursor key crsr_test_key_1234567890",
        member.dingtalk_user_id,
        config,
        repo,
    )
    assert reply is not None
    assert "加密密钥" in reply


@patch("pulse.ingestion.sync.CursorSyncService")
@patch("pulse.ingestion.credentials.CredentialService")
def test_bind_command_success(mock_cred_cls, mock_sync_cls, bot_repo):
    repo, member, cursor = bot_repo
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )

    cred = MagicMock()
    cred.key_hint = "crsr_...7890"
    mock_cred_cls.return_value.bind_cursor_api_key.return_value = cred
    mock_sync_cls.return_value.sync_account.return_value = MagicMock(event_count=3)

    reply = handle_bind_cursor_command(
        "绑定 cursor key crsr_test_key_1234567890",
        member.dingtalk_user_id,
        config,
        repo,
    )
    assert reply is not None
    assert "已绑定" in reply
    assert cursor.account_identifier in reply


def test_pending_review_manual_only(bot_repo):
    repo, member, _cursor = bot_repo
    from pulse.storage.models import UsageIngestion

    manual = UsageIngestion(
        member_id=member.id,
        billing_period="2026-06",
        source_type="manual_vision",
        channel="dingtalk",
        status="pending_review",
        event_count=0,
    )
    api_pending = UsageIngestion(
        member_id=member.id,
        billing_period="2026-06",
        source_type="api_sync",
        channel="scheduler",
        status="pending_review",
        event_count=0,
    )
    repo.session.add(manual)
    repo.session.add(api_pending)
    repo.commit()

    config = AppConfig(tenant=TenantConfig(slug="test", name="Test"))
    config.admin.dingtalk_user_ids = [member.dingtalk_user_id]
    reply = run_command("待审 2026-06", member.dingtalk_user_id, config, repo)
    assert manual.id[:8] in reply
    assert api_pending.id[:8] not in reply


def test_cursor_bind_guide_message():
    assert "绑定 cursor key" in CURSOR_BIND_GUIDE
