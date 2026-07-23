from __future__ import annotations

def _msg(result):
    data = result.result or {}
    return result.user_message or data.get("text") or data.get("answer") or ""


from unittest.mock import MagicMock, patch

import pytest

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.invoke import invoke_capability
from pulse.config import AppConfig, CollectionConfig, TenantConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def _invoke_request(*, member_id: str, team_id: str, capability_key: str, arguments: dict | None = None):
    return CapabilityInvokeRequest(
        invocation_id="inv-1",
        idempotency_key="idem-1",
        capability_key=capability_key,
        capability_version="1",
        team_id=team_id,
        actor_member_id=member_id,
        arguments=arguments or {},
        confirmed_by=member_id,
    )


def test_owner_can_publish_report_without_dingtalk_admin_ids():
    session = init_db("sqlite:///:memory:")()
    try:
        _team, repo = make_team_repo(session)
        member = repo.add_member("owner-user", "Owner")
        member.portal_role = "owner"
        repo.commit()

        config = AppConfig(tenant=TenantConfig(slug="test", name="Test"))
        config.admin.dingtalk_user_ids = []

        with patch(
            "pulse.capabilities.handlers.text_capabilities.publish_report_to_group",
            return_value="月报正文",
        ):
            result = invoke_capability(
                session,
                request=_invoke_request(
                    member_id=member.id,
                    team_id=repo.team_id,
                    capability_key="report.publish",
                    arguments={"period": "2026-07"},
                ),
                config=config,
            )

        assert result.status == "succeeded"
        assert "无权限" not in _msg(result)
        assert "暂未发群" in _msg(result)
        assert "月报正文" in _msg(result)
    finally:
        session.close()


def test_owner_can_publish_report_to_group_when_enabled():
    session = init_db("sqlite:///:memory:")()
    try:
        _team, repo = make_team_repo(session)
        member = repo.add_member("owner-user", "Owner")
        member.portal_role = "owner"
        repo.commit()

        config = AppConfig(
            tenant=TenantConfig(slug="test", name="Test"),
            collection=CollectionConfig(publish_report_to_group=True),
        )
        config.admin.dingtalk_user_ids = []
        messenger = MagicMock()

        messenger = MagicMock()
        with (
            patch(
                "pulse.capabilities.handlers.text_capabilities.publish_report_to_group",
                return_value="月报正文",
            ),
            patch(
                "pulse.capabilities.handlers.text_capabilities._optional_messenger",
                return_value=messenger,
            ),
        ):
            result = invoke_capability(
                session,
                request=_invoke_request(
                    member_id=member.id,
                    team_id=repo.team_id,
                    capability_key="report.publish",
                    arguments={"period": "2026-07"},
                ),
                config=config,
            )

        assert result.status == "succeeded"
        assert "无权限" not in _msg(result)
        assert "月报已发布" in _msg(result)
    finally:
        session.close()

from assistant_platform.conversation.intents import match_capability_intent
from pulse.channels.commands import _looks_like_help, _looks_like_self_loan_read
from pulse.channels.dingtalk.handler import DingTalkChannelHandler
from pulse.config import AppConfig, AssistantMirrorConfig, TenantConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


@pytest.mark.parametrize(
    "text,key",
    [
        ("额度", "quota.self.read"),
        ("我的额度", "quota.self.read"),
        ("我的", "submission.self.read"),
        ("状态", "submission.status.read"),
        ("帮助", "bot.help"),
        ("你有什么功能", None),
        ("你能提供什么帮助", None),
        ("我的用量", "usage.self.read"),
        ("查询 我的用量", None),
        ("聚合 2026-07", "usage.aggregate"),
        ("报告", "report.publish"),
        ("成员", "members.manage"),
        ("告警", "alerts.run"),
        ("导出", "usage.export"),
        ("上报 智谱 85", "usage.manual.submit"),
        ("借 Key 不够用了", None),
        ("归还 Key", "key.loan.return"),
        ("我的借用", "key.loan.self.read"),
        ("借用列表", "key.loan.list"),
        ("撤销借用 abc12345", "key.loan.revoke"),
        ("解绑 cursor", "cursor.key.unbind"),
    ],
)
def test_match_capability_intent_covers_commands(text, key):
    intent = match_capability_intent(text)
    if key is None:
        assert intent is None
        return
    assert intent is not None
    assert intent.capability_key == key


@pytest.mark.parametrize(
    "text",
    [
        "我借的key",
        "我借的 Key",
        "借入的key",
        "查看我借用的key",
        "申请的key用量怎么样",
        "借用的key用了多少",
        "借 Key 不够用了",
        "借用临时 Key",
    ],
)
def test_fuzzy_phrases_defer_to_llm(text):
    assert match_capability_intent(text) is None


@pytest.mark.parametrize(
    "text,key",
    [
        ("借用状态", "key.loan.self.read"),
        ("借用列表", "key.loan.list"),
        ("借key", "key.loan.request"),
        ("借 Key", "key.loan.request"),
    ],
)
def test_exact_loan_commands(text, key):
    intent = match_capability_intent(text)
    assert intent is not None
    assert intent.capability_key == key


def test_lent_out_phrase_not_self_read():
    intent = match_capability_intent("我借出的key")
    assert intent is None


def test_loan_usage_query_defers_to_llm():
    assert match_capability_intent("申请的key用量怎么样") is None
    assert match_capability_intent("申请 Cursor") is None


def test_looks_like_self_loan_read_helper():
    assert _looks_like_self_loan_read("我借的key") is True
    assert _looks_like_self_loan_read("查看我借用的key") is True
    assert _looks_like_self_loan_read("申请的key用量怎么样") is True
    assert _looks_like_self_loan_read("我借出的key") is False
    assert _looks_like_self_loan_read("借用列表") is False
    assert _looks_like_self_loan_read("申请key") is False


def test_looks_like_help_natural_phrases():
    assert _looks_like_help("帮助") is True
    assert _looks_like_help("你有什么功能") is True
    assert _looks_like_help("你能提供什么帮助") is True
    assert _looks_like_help("有哪些功能") is True
    assert _looks_like_help("怎么用") is True
    assert _looks_like_help("帮助 绑定") is True
    assert _looks_like_help("绑定 怎么用") is True
    assert _looks_like_help("查询 谁用得最多") is False
    assert _looks_like_help("借 Key 不够用了") is False


def test_help_detail_intent_carries_topic():
    intent = match_capability_intent("帮助 借 Key")
    assert intent is not None
    assert intent.capability_key == "bot.help"
    assert intent.arguments.get("topic") == "borrow"


def test_natural_help_defer_to_llm():
    assert match_capability_intent("你有什么功能") is None
    assert match_capability_intent("怎么用") is None


def test_intent_usage_deferred_to_llm():
    assert match_capability_intent("查询 我的用量") is None


def test_intent_my_usage_exact():
    intent = match_capability_intent("我的用量")
    assert intent is not None
    assert intent.capability_key == "usage.self.read"


@pytest.mark.asyncio
async def test_channel_never_runs_legacy_command_reply():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        ),
    )
    session = init_db("sqlite:///:memory:")()
    _team, repo = make_team_repo(session)
    repo.get_or_create_member("u1", "Alice")
    session.commit()

    handler = DingTalkChannelHandler(
        config=config,
        session_factory=lambda: session,
        messenger=MagicMock(),
    )
    incoming = MagicMock()
    incoming.conversation_type = "1"
    incoming.is_in_at_list = True
    incoming.sender_staff_id = "u1"
    incoming.sender_id = "u1"
    incoming.sender_nick = "Alice"
    incoming.conversation_id = "u1"
    incoming.message_id = "msg-1"
    incoming.text.content = "查询 我的用量"
    incoming.message_type = "text"

    with (
        patch("pulse.channels.dingtalk.mirror.mirror_dingtalk_message") as mirror,
        patch.object(handler, "reply_text") as reply,
    ):
        await handler._handle_message(incoming, {})
        mirror.assert_called_once()
        reply.assert_not_called()
    assert not hasattr(handler, "_handle_command")
    assert not hasattr(handler, "_handle_conversational_text")
    assert not hasattr(handler, "_handle_tip")
