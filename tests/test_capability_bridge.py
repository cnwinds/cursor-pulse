from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from assistant_platform.contracts.provider import CapabilityInvokeResult
from pulse.channels.capability_bridge import (
    format_capability_reply,
    invoke_capability_local,
    invoke_via_assistant,
)
from pulse.channels.commands import _handle_quota_command, handle_bind_cursor_command
from pulse.config import (
    AppConfig,
    AssistantMirrorConfig,
    CapabilityBridgeConfig,
    CredentialConfig,
    TenantConfig,
    load_config,
)
from pulse.storage.db import init_db
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def _bridge_config(**flags: bool) -> AppConfig:
    return AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        assistant_mirror=AssistantMirrorConfig(
            base_url="http://assistant.test",
            service_token="svc-tok",
            timeout_seconds=5.0,
        ),
        capability_bridge=CapabilityBridgeConfig(**flags),
    )


def test_invoke_via_assistant_posts_and_returns_user_message():
    cfg = _bridge_config()
    with patch("pulse.channels.capability_bridge.httpx.Client") as Client:
        client = Client.return_value.__enter__.return_value
        client.post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=lambda: {"status": "succeeded", "user_message": "额度充足"},
        )
        msg = invoke_via_assistant(
            config=cfg,
            team_id="team-1",
            member_id="mem-1",
            role="ai_member",
            capability_key="quota.self.read",
            arguments={},
            confirmed=True,
        )
        assert msg == "额度充足"
        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args[0] == "http://assistant.test/api/assistant/v1/capabilities/invoke"
        assert kwargs["headers"]["Authorization"] == "Bearer svc-tok"
        payload = kwargs["json"]
        assert payload["team_id"] == "team-1"
        assert payload["actor_member_id"] == "mem-1"
        assert payload["capability_key"] == "quota.self.read"
        assert payload["confirmed"] is True


def test_invoke_via_assistant_raises_on_http_error():
    cfg = _bridge_config()
    with patch("pulse.channels.capability_bridge.httpx.Client") as Client:
        client = Client.return_value.__enter__.return_value
        response = MagicMock(status_code=500)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=response
        )
        client.post.return_value = response
        with pytest.raises(httpx.HTTPStatusError):
            invoke_via_assistant(
                config=cfg,
                team_id="t",
                member_id="m",
                role=None,
                capability_key="quota.self.read",
                arguments={},
            )


def test_load_config_reads_capability_bridge_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPABILITY_BRIDGE_QUOTA_SELF_READ", "true")
    monkeypatch.setenv("CAPABILITY_BRIDGE_CURSOR_KEY_BIND", "1")
    monkeypatch.setenv("CAPABILITY_BRIDGE_GUIDE_IMAGE_UPDATE", "yes")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("tenant:\n  slug: env-test\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.capability_bridge.quota_self_read is True
    assert cfg.capability_bridge.cursor_key_bind is True
    assert cfg.capability_bridge.guide_image_update is True


def test_format_capability_reply_usage_payload_does_not_use_quota_keys():
    """Regression: usage accounts use identifier, not account_identifier (was KeyError → 500)."""
    result = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "query": {"mode": "billing_cycle", "period": "2026-07"},
            "accounts": [
                {
                    "kind": "owned",
                    "identifier": "user@example.com",
                    "window_label": "记账周期",
                    "range_text": "06-24 ~ 07-24",
                    "events": 2,
                    "tokens": 100,
                    "cost_usd": 1.5,
                    "models": [{"model": "gpt", "events": 2, "tokens": 100, "cost_usd": 1.5}],
                    "data_updated_at": None,
                    "is_loan": False,
                }
            ],
            "empty_reason": None,
        },
    )
    msg = format_capability_reply(result)
    assert "你的用量" in msg
    assert "user@example.com" in msg
    assert "Cursor 额度" not in msg


def test_format_capability_reply_quota_payload_still_works():
    result = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "accounts": [
                {
                    "account_id": "a1",
                    "account_identifier": "quota@example.com",
                    "has_snapshot": False,
                    "status": "unknown",
                }
            ],
            "empty_reason": None,
        },
    )
    msg = format_capability_reply(result)
    assert "Cursor 额度" in msg
    assert "quota@example.com" in msg


def test_format_capability_reply_usage_empty_reason():
    result = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "query": {"mode": "billing_cycle", "period": "2026-07"},
            "accounts": [],
            "empty_reason": "no_cursor_or_loan",
        },
    )
    assert "Key 借用" in format_capability_reply(result)


@pytest.fixture
def bot_repo():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    member = repo.add_member("dt-quota", "Quota User")
    tool_repo = ToolCenterRepository(session, team.id)
    cursor = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(cursor.id, primary_member_id=member.id)
    session.commit()
    yield repo, member
    session.close()


def test_quota_command_uses_local_when_flag_off(bot_repo):
    repo, member = bot_repo
    config = _bridge_config()
    with patch("pulse.channels.capability_bridge.invoke_via_assistant") as bridge:
        reply = _handle_quota_command("额度", member.dingtalk_user_id, config, repo)
        bridge.assert_not_called()
    assert "Cursor 额度" in reply or "尚未绑定" in reply


def test_quota_command_uses_bridge_when_flag_on(bot_repo):
    repo, member = bot_repo
    config = _bridge_config(quota_self_read=True)
    with patch(
        "pulse.channels.capability_bridge.invoke_via_assistant",
        return_value="桥接额度回复",
    ) as bridge:
        reply = _handle_quota_command("额度", member.dingtalk_user_id, config, repo)
        bridge.assert_called_once()
        assert bridge.call_args.kwargs["capability_key"] == "quota.self.read"
    assert reply == "桥接额度回复"


def test_quota_command_falls_back_when_bridge_fails(bot_repo):
    repo, member = bot_repo
    config = _bridge_config(quota_self_read=True)
    with patch(
        "pulse.channels.capability_bridge.invoke_via_assistant",
        side_effect=httpx.ConnectError("down"),
    ):
        reply = _handle_quota_command("额度", member.dingtalk_user_id, config, repo)
    assert "Cursor 额度" in reply or "尚未绑定" in reply


@patch("pulse.ingestion.sync.CursorSyncService")
@patch("pulse.ingestion.credentials.CredentialService")
def test_bind_command_uses_bridge_when_flag_on(mock_cred_cls, mock_sync_cls, bot_repo):
    repo, member, = bot_repo
    config = _bridge_config(cursor_key_bind=True)
    with patch(
        "pulse.channels.capability_bridge.invoke_via_assistant",
        return_value="桥接绑定成功",
    ) as bridge:
        reply = handle_bind_cursor_command(
            "绑定 cursor key crsr_test_key_1234567890",
            member.dingtalk_user_id,
            config,
            repo,
        )
        bridge.assert_called_once()
        assert bridge.call_args.kwargs["capability_key"] == "cursor.key.bind"
        assert bridge.call_args.kwargs["arguments"]["api_key"] == "crsr_test_key_1234567890"
    assert reply == "桥接绑定成功"
    mock_cred_cls.assert_not_called()


@patch("pulse.ingestion.sync.CursorSyncService")
@patch("pulse.ingestion.credentials.CredentialService")
def test_bind_command_falls_back_when_bridge_fails(mock_cred_cls, mock_sync_cls, bot_repo):
    repo, member = bot_repo
    config = _bridge_config(cursor_key_bind=True)
    cred = MagicMock()
    cred.key_hint = "crsr_...7890"
    mock_cred_cls.return_value.bind_cursor_api_key.return_value = cred
    mock_sync_cls.return_value.sync_account.return_value = MagicMock(event_count=2)
    with patch(
        "pulse.channels.capability_bridge.invoke_via_assistant",
        side_effect=httpx.ConnectError("down"),
    ):
        reply = handle_bind_cursor_command(
            "绑定 cursor key crsr_test_key_1234567890",
            member.dingtalk_user_id,
            config,
            repo,
        )
    assert reply is not None
    assert "已绑定" in reply


def test_invoke_capability_local_quota(bot_repo):
    repo, member = bot_repo
    config = _bridge_config()
    msg = invoke_capability_local(
        repo.session,
        config=config,
        team_id=repo.team_id,
        member_id=member.id,
        capability_key="quota.self.read",
        arguments={},
    )
    assert "Cursor 额度" in msg or "尚未绑定" in msg


def test_guide_image_handler_uses_bridge_when_flag_on(tmp_path):
    from pulse.channels.dingtalk.handler import DingTalkChannelHandler

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    config = _bridge_config(guide_image_update=True)
    config.storage.raw_files_dir = str(raw_dir)
    config.dingtalk.app_key = "k"
    config.dingtalk.app_secret = "s"
    config.dingtalk.robot_code = "r"

    dest = raw_dir / "inbox" / "cursor_bind_key_guide.png"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    messenger = MagicMock()

    def _download(_code, path):
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    messenger.download_message_file.side_effect = _download

    handler = DingTalkChannelHandler(
        config=config,
        session_factory=MagicMock(),
        messenger=messenger,
    )
    handler._pending_guide_upload.add("admin-1")

    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    admin = repo.add_member("admin-1", "Admin")
    config.admin.dingtalk_user_ids = ["admin-1"]
    session.commit()
    handler.session_factory = MagicMock(return_value=session)

    incoming = MagicMock()
    with (
        patch("pulse.channels.dingtalk.handler.inbox_dest", return_value=dest),
        patch(
            "pulse.channels.capability_bridge.invoke_via_assistant",
            return_value="引导图已更新",
        ) as bridge,
    ):
        import asyncio

        asyncio.run(
            handler._save_guide_image_from_picture(
                "dl-code", incoming, "admin-1", is_group=False
            )
        )
        bridge.assert_called_once()
        assert bridge.call_args.kwargs["capability_key"] == "guide_image.update"
        assert "image_base64" in bridge.call_args.kwargs["arguments"]
    messenger.download_message_file.assert_called_once()
    session.close()
