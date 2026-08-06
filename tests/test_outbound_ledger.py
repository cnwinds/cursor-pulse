"""Tests for Pulse outbound ledger client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pulse.channels import outbound_ledger as ol
from pulse.config import (
    AppConfig,
    AssistantMirrorConfig,
    BotPlatformConfig,
    DingTalkConfig,
    TenantConfig,
)


def _config(*, mirror_enabled: bool = True) -> AppConfig:
    return AppConfig(
        tenant=TenantConfig(slug="t", name="T"),
        bot=BotPlatformConfig(name="dingtalk"),
        dingtalk=DingTalkConfig(group_open_conversation_id="cid-group"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=mirror_enabled,
            base_url="http://assistant.test",
            service_token="tok",
            timeout_seconds=2.0,
        ),
    )


def test_send_oto_and_ledger_posts_when_delivered():
    config = _config(mirror_enabled=True)
    messenger = MagicMock()
    messenger.send_oto_text.return_value = {"ok": True}

    with patch.object(ol, "record_outbound_ledger") as record:
        result = ol.send_oto_and_ledger(
            config,
            messenger,
            user_id="u1",
            text="hello",
            source="key_loan.issued",
            team_id="team-1",
        )

    assert result == {"ok": True}
    messenger.send_oto_text.assert_called_once_with("u1", "hello")
    record.assert_called_once()
    kwargs = record.call_args.kwargs
    assert kwargs["source"] == "key_loan.issued"
    assert kwargs["user_id"] == "u1"
    assert kwargs["conversation_type"] == "private"


def test_send_oto_and_ledger_skips_record_when_skipped():
    config = _config(mirror_enabled=True)
    messenger = MagicMock()
    messenger.send_oto_text.return_value = {"ok": True, "skipped": True}

    with patch.object(ol, "record_outbound_ledger") as record:
        ol.send_oto_and_ledger(
            config,
            messenger,
            user_id="u1",
            text="hello",
            source="key_loan.issued",
            team_id="team-1",
        )

    record.assert_not_called()


def test_send_oto_and_ledger_skips_when_mirror_disabled():
    config = _config(mirror_enabled=False)
    messenger = MagicMock()
    messenger.send_oto_text.return_value = {"ok": True}

    with patch.object(ol, "record_outbound_ledger") as record:
        with patch.object(ol, "resolve_team_id") as resolve:
            ol.send_oto_and_ledger(
                config,
                messenger,
                user_id="u1",
                text="hello",
                source="key_loan.issued",
            )

    resolve.assert_not_called()
    record.assert_not_called()
    messenger.send_oto_text.assert_called_once()


def test_send_group_and_ledger_posts():
    config = _config(mirror_enabled=True)
    messenger = MagicMock()
    messenger.send_group_text.return_value = {"ok": True}

    with patch.object(ol, "record_outbound_ledger") as record:
        ol.send_group_and_ledger(
            config,
            messenger,
            text="digest",
            source="knowledge.digest",
            team_id="team-1",
        )

    record.assert_called_once()
    assert record.call_args.kwargs["conversation_type"] == "group"
    assert record.call_args.kwargs["conversation_id"] == "cid-group"


def test_record_outbound_ledger_http(monkeypatch):
    config = _config(mirror_enabled=True)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "recorded", "message_id": "m1"}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            assert url.endswith("/api/assistant/v1/ledger/outbound")
            assert json["source"] == "key_loan.issued"
            assert headers["Authorization"] == "Bearer tok"
            return mock_resp

    monkeypatch.setattr(ol, "internal_client", lambda timeout: _Client())
    body = ol.record_outbound_ledger(
        config,
        team_id="team-1",
        channel="dingtalk",
        conversation_type="private",
        user_id="u1",
        text="hi",
        source="key_loan.issued",
    )
    assert body["status"] == "recorded"


def test_notify_loan_issued_records_ledger(monkeypatch):
    from pulse.tool_center import key_loan_notify as notify

    messenger = MagicMock()
    messenger.send_oto_text.return_value = {"ok": True}
    monkeypatch.setattr(notify, "outbound_messenger_or_none", lambda config: messenger)
    monkeypatch.setattr(
        notify,
        "team_repository",
        lambda session, config: (SimpleNamespace(id="team-1"), None),
    )

    recorded = []

    def _capture(*args, **kwargs):
        recorded.append(kwargs)
        return {"status": "recorded"}

    monkeypatch.setattr(ol, "record_outbound_ledger", _capture)
    monkeypatch.setattr(notify, "send_oto_and_ledger", ol.send_oto_and_ledger)

    config = _config(mirror_enabled=True)
    config.admin.channel_user_ids = ["dt-admin"]
    session = MagicMock()
    borrower = SimpleNamespace(
        id="m-borrower",
        display_name="Borrower",
        channel="dingtalk",
        channel_user_id="dt-borrower",
    )
    session.get.return_value = borrower

    with patch(
        "pulse.identity.service.external_id_for",
        side_effect=lambda _s, member, channel: (
            "dt-borrower" if channel == "dingtalk" and member is borrower else None
        ),
    ):
        notify.notify_loan_issued(
            session,
            config,
            result={
                "loan_id": "loan-abcdef",
                "api_key": "pka_" + ("a" * 32),
                "borrower_member_id": "m-borrower",
                "borrower_name": "Borrower",
                "loan_expires_on": "2026-08-01",
                "delivery_mode": "proxy_alias",
            },
            skip_borrower=False,
        )

    assert messenger.send_oto_text.call_count == 2
    assert len(recorded) == 2
    sources = {r["source"] for r in recorded}
    assert sources == {"key_loan.issued"}
    users = {r["user_id"] for r in recorded}
    assert users == {"dt-borrower", "dt-admin"}
