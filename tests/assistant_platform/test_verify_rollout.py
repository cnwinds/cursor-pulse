"""Phase 7 integration tests: end-to-end flows, isolation, degrade, observability."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("fastapi")

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import (
    AssistantChatMemoryConfig,
    AssistantConfig,
    AssistantLlmConfig,
    MemoryFeatureFlags,
    MemoryRecallBudgetConfig,
)
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.orchestrator import (
    generate_reply_text,
    process_session_close_job,
)
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.memory.archive_pipeline import run_archive_pipeline
from assistant_platform.memory.archive_search import hybrid_search, resolve_search_scope
from assistant_platform.memory.context_builder import build_recall_bundle
from assistant_platform.memory.contracts import ArchivePipelineStatus
from assistant_platform.memory.opt_out import set_memory_opt_out
from assistant_platform.memory.agent_tools import MemoryToolService
from assistant_platform.profiles.compiler import compile_profile_guidance
from assistant_platform.profiles.models import ProfileSignalRow
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow
from assistant_platform.memory.semantic.domain import VisibilityContext
from pulse.capabilities.invoke import invoke_capability
from pulse.config import WebSearchConfig, load_config
from pulse.storage.db import init_db
from pulse.storage.models import Member
from tests.conftest import make_team_repo

SERVICE_TOKEN = "assistant-secret"
TEAM_A = "team-rollout-a"
TEAM_B = "team-rollout-b"


@pytest.fixture(autouse=True)
def _isolate_team_settings(monkeypatch):
    """These tests build AssistantConfig in-process; don't let a real dev
    ``data/pulse.db`` team_settings row (chat_memory/assistant_llm overrides)
    leak in and silently re-enable OpenAI embeddings against a fake api key."""
    monkeypatch.setattr(
        "pulse.team_settings_loader.read_team_setting_section",
        lambda *, team_slug, section, database_url=None: {},
    )


def _session_row(**overrides) -> ChatSessionRow:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    data = dict(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_A,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
        status="open",
        opened_at=now,
        last_activity_at=now,
    )
    data.update(overrides)
    return ChatSessionRow(**data)


def _msg(session_id: str, role: str, text: str, *, kind: str | None = None, offset: int = 0) -> ChatMessageRow:
    from datetime import timedelta

    base = datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)
    return ChatMessageRow(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role=role,
        text_redacted=text,
        meta_json={"kind": kind} if kind is not None else {},
        created_at=base,
    )


def _incoming(*, team_id: str = TEAM_A, user_id: str = "user-a", text: str = "hello") -> IncomingEventRow:
    return IncomingEventRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=team_id,
        channel="dingtalk",
        sender_channel_user_id=user_id,
        sender_display_name="Tester",
        text_redacted=text,
        reply_endpoint_json={"member_id": user_id},
    )


def _memory_config(**flag_overrides) -> AssistantChatMemoryConfig:
    flags = MemoryFeatureFlags(
        archive_pipeline=True,
        auto_recall_per_turn=True,
        profile_compile=True,
    )
    for key, value in flag_overrides.items():
        setattr(flags, key, value)
    from assistant_platform.config import MemoryEmbeddingConfig

    return AssistantChatMemoryConfig(
        recall=MemoryRecallBudgetConfig(expand_neighbor_count=1, context_token_budget=800),
        embedding=MemoryEmbeddingConfig(enabled=False),
        features=flags,
    )


def _assistant_config(*, team_id: str = TEAM_A) -> AssistantConfig:
    # LLM stays disabled here so the archive pipeline's facts stage falls back
    # to RuleBasedDistiller (these tests rely on its "事实:"/"偏好:" parsing)
    # instead of making a real network call with a fake api key.
    return AssistantConfig(
        service_token=SERVICE_TOKEN,
        team_id=team_id,
        memory_enabled=True,
        llm=AssistantLlmConfig(enabled=False, api_key="k", model="m"),
        chat_memory=_memory_config(),
    )


def _close_and_archive(db, session_row: ChatSessionRow, config: AssistantConfig | None = None) -> SessionArchiveRow:
    close_session(db, session_row, reason="manual", enqueue_close_job=False)
    db.commit()
    cfg = config or _assistant_config(team_id=session_row.team_id)
    archive = run_archive_pipeline(db, config=cfg, session_row=session_row)
    db.commit()
    return archive


def test_e2e_close_archive_recall_expand(caplog):
    """关闭会话 → 归档 ready → 新回合自动召回 → 工具展开。"""
    caplog.set_level(logging.INFO)
    Session = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = Session()

    closed = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    open_row = _session_row()
    for row, keyword in ((closed, "rollout-nebula"), (open_row, "current-turn")):
        db.add(row)
        db.add(_msg(row.id, "user", f"{keyword} unique marker", offset=1))
        db.add(_msg(row.id, "assistant", "ack", kind="final", offset=2))
    db.commit()

    archive = _close_and_archive(db, closed)
    assert archive.status == ArchivePipelineStatus.READY.value

    captured: dict = {}

    class CaptureLlm:
        def complete_with_tools(self, *, messages, tools, temperature=0.1):
            captured["system"] = messages[0]["content"]
            return {
                "content": "reply",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "reply"},
            }

    from assistant_platform import config as config_module

    base_config = config_module.AssistantConfig(
        llm=config_module.AssistantLlmConfig(enabled=True, api_key="k", model="m"),
        chat_memory=_memory_config(),
    )

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=CaptureLlm(),
    ), patch(
        "assistant_platform.conversation.orchestrator.resolve_capabilities",
        return_value=[],
    ), patch(
        "assistant_platform.conversation.orchestrator.compose_system_supplement",
        return_value="",
    ):
        reply = generate_reply_text(
            db,
            config=base_config,
            incoming=_incoming(text="tell me about rollout-nebula"),
            text="tell me about rollout-nebula",
            session_row=open_row,
        )

    assert reply == "reply"
    system = captured.get("system", "")
    assert "rollout-nebula" in system.lower()

    scope = resolve_search_scope(
        team_id=TEAM_A,
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    service = MemoryToolService(
        db,
        config=_memory_config(),
        scope=scope,
        visibility_context=VisibilityContext.private("user-a"),
    )
    search = service.search("rollout-nebula")
    assert search["ok"] is True
    hit = search["result"]["fragments"][0]
    expand = service.expand(
        session_id=hit["session_id"],
        chunk_index=hit["chunk_index"],
        start_seq=hit["start_seq"],
        end_seq=hit["end_seq"],
    )
    assert expand["ok"] is True

    recall_logs = [r.message for r in caplog.records if "event=recall_bundle" in r.message]
    expand_logs = [r.message for r in caplog.records if "event=memory_tool" in r.message and "memory_expand" in r.message]
    assert recall_logs
    assert expand_logs
    assert "rollout-nebula" not in " ".join(recall_logs)
    db.close()


def test_private_group_cross_team_isolation():
    """私聊/群聊/跨团队严格隔离。"""
    Session = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = Session()
    keyword = "isolation-secret-token"

    private_a = _session_row(
        status="closed",
        closed_at=datetime.now(timezone.utc),
        conversation_type="private",
        user_id="user-a",
        conversation_id="user-a",
    )
    group_a = _session_row(
        id=str(uuid.uuid4()),
        status="closed",
        closed_at=datetime.now(timezone.utc),
        conversation_type="group",
        user_id="user-a",
        conversation_id="group-1",
    )
    private_b_team = _session_row(
        id=str(uuid.uuid4()),
        status="closed",
        closed_at=datetime.now(timezone.utc),
        team_id=TEAM_B,
        user_id="user-a",
        conversation_id="user-a",
    )
    for row in (private_a, group_a, private_b_team):
        db.add(row)
        db.add(_msg(row.id, "user", f"{keyword} for {row.conversation_type}", offset=1))
        db.add(_msg(row.id, "assistant", "ok", kind="final", offset=2))
    db.commit()
    for row in (private_a, group_a, private_b_team):
        _close_and_archive(db, row, _assistant_config(team_id=row.team_id))

    private_scope = resolve_search_scope(
        team_id=TEAM_A,
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    private_hits, page = hybrid_search(
        db, query=keyword, scope=private_scope, config=_memory_config()
    )
    assert page.total_hits == 1
    assert private_hits[0].session_id == private_a.id

    group_scope = resolve_search_scope(
        team_id=TEAM_A,
        subject_id="user-a",
        conversation_type="group",
        conversation_id="group-1",
        user_id="user-a",
    )
    group_hits, group_page = hybrid_search(
        db, query=keyword, scope=group_scope, config=_memory_config()
    )
    assert group_page.total_hits == 1
    assert group_hits[0].session_id == group_a.id

    cross_team_scope = resolve_search_scope(
        team_id=TEAM_A,
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    cross_hits, cross_page = hybrid_search(
        db, query=keyword, scope=cross_team_scope, config=_memory_config()
    )
    session_ids = {h.session_id for h in cross_hits}
    assert private_b_team.id not in session_ids
    db.close()


def test_recall_degrades_when_search_fails(monkeypatch, caplog):
    """索引不可用时不阻塞聊天，召回标记 degraded。"""
    caplog.set_level(logging.INFO)
    Session = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = Session()
    closed = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    db.add(closed)
    db.add(_msg(closed.id, "user", "degrade-marker phrase", offset=1))
    db.add(_msg(closed.id, "assistant", "ok", kind="final", offset=2))
    db.commit()
    _close_and_archive(db, closed)

    def boom(*_a, **_k):
        raise RuntimeError("fts unavailable")

    monkeypatch.setattr(
        "assistant_platform.memory.context_builder.hybrid_search",
        boom,
    )
    monkeypatch.setattr(
        "assistant_platform.memory.context_builder.recall_fact_items",
        lambda *_a, **_k: [],
    )

    scope = resolve_search_scope(
        team_id=TEAM_A,
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    bundle = build_recall_bundle(
        db,
        query="degrade-marker",
        scope=scope,
        config=_memory_config(),
        visibility_context=VisibilityContext.private("user-a"),
    )
    assert bundle.degraded is True

    captured: dict = {}

    class CaptureLlm:
        def complete_with_tools(self, *, messages, tools, temperature=0.1):
            captured["system"] = messages[0]["content"]
            return {
                "content": "ok without memory",
                "tool_calls": [],
                "raw_assistant_message": {"role": "assistant", "content": "ok without memory"},
            }

    open_row = _session_row()
    db.add(open_row)
    db.commit()

    from assistant_platform import config as config_module

    base_config = config_module.AssistantConfig(
        llm=config_module.AssistantLlmConfig(enabled=True, api_key="k", model="m"),
        chat_memory=_memory_config(),
    )
    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=CaptureLlm(),
    ), patch(
        "assistant_platform.conversation.orchestrator.resolve_capabilities",
        return_value=[],
    ), patch(
        "assistant_platform.conversation.orchestrator.compose_system_supplement",
        return_value="",
    ):
        reply = generate_reply_text(
            db,
            config=base_config,
            incoming=_incoming(text="degrade-marker"),
            text="degrade-marker",
            session_row=open_row,
        )
    assert reply == "ok without memory"
    system = captured.get("system", "")
    assert "相关片段" not in system
    assert "[rank=" not in system

    degrade_logs = [r.message for r in caplog.records if "degraded=true" in r.message]
    assert degrade_logs
    db.close()


def test_profile_correction_affects_next_recall():
    """用户纠正偏好后下一回合召回使用新画像。"""
    cfg = _assistant_config()
    sf = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = sf()
    session_row, _ = attach_user_message(
        db,
        IncomingMessageEvent(
            event_id=str(uuid.uuid4()),
            channel="dingtalk",
            channel_message_id=str(uuid.uuid4()),
            assistant_id="xiaomai",
            team_id=TEAM_A,
            sender_channel_user_id="user-a",
            sender_display_name="Alice",
            conversation_type="private",
            conversation_id="user-a",
            text_redacted="偏好: 详细回复",
            occurred_at=datetime.now(timezone.utc),
        ),
    )
    db.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="noted",
            meta_json={"kind": "final"},
        )
    )
    close_session(db, session_row, reason="manual", enqueue_close_job=False)
    db.commit()
    process_session_close_job(db, {"session_id": session_row.id}, cfg)
    db.commit()

    signal = db.scalar(select(ProfileSignalRow).where(ProfileSignalRow.user_id == "user-a"))
    assert signal is not None

    app = create_assistant_app(cfg, sf)
    test_client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Channel-User-Id": "user-a",
        "X-Pulse-Actor-Permissions": "assistant:sessions:read:self",
    }
    resp = test_client.post(
        "/api/assistant/v1/profiles/corrections",
        headers=headers,
        json={
            "user_id": "user-a",
            "team_id": TEAM_A,
            "signal_id": signal.id,
            "correction_text": "请保持简洁",
        },
    )
    assert resp.status_code == 200

    profile = compile_profile_guidance(db, user_id="user-a", team_id=TEAM_A)
    assert profile is not None
    assert profile.items[0].guidance == "请保持简洁"
    db.close()


def test_memory_opt_out_blocks_archive():
    """opt-out 后关闭会话不再归档。"""
    cfg = _assistant_config()
    sf = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = sf()
    set_memory_opt_out(db, user_id="user-a", team_id=TEAM_A)
    db.commit()

    session_row, _ = attach_user_message(
        db,
        IncomingMessageEvent(
            event_id=str(uuid.uuid4()),
            channel="dingtalk",
            channel_message_id=str(uuid.uuid4()),
            assistant_id="xiaomai",
            team_id=TEAM_A,
            sender_channel_user_id="user-a",
            sender_display_name="Alice",
            conversation_type="private",
            conversation_id="user-a",
            text_redacted="opt-out test",
            occurred_at=datetime.now(timezone.utc),
        ),
    )
    close_session(db, session_row, reason="manual", enqueue_close_job=False)
    db.commit()
    with pytest.raises(RuntimeError, match="opt-out"):
        run_archive_pipeline(db, config=cfg, session_row=session_row)
    db.close()


def test_delete_cascade_removes_search_hits():
    """级联删除后搜索不可再命中。"""
    cfg = _assistant_config()
    sf = init_assistant_db("sqlite://", team_id=TEAM_A)
    app = create_assistant_app(cfg, sf)
    test_client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pulse-Actor-Member-Id": "mem-1",
        "X-Pulse-Actor-Role": "operator",
        "X-Pulse-Actor-Channel-User-Id": "user-a",
        "X-Pulse-Actor-Permissions": (
            "assistant:sessions:read:self,"
            "assistant:sessions:delete:self"
        ),
    }

    db = sf()
    session_row, _ = attach_user_message(
        db,
        IncomingMessageEvent(
            event_id=str(uuid.uuid4()),
            channel="dingtalk",
            channel_message_id=str(uuid.uuid4()),
            assistant_id="xiaomai",
            team_id=TEAM_A,
            sender_channel_user_id="user-a",
            sender_display_name="Alice",
            conversation_type="private",
            conversation_id="user-a",
            text_redacted="cascade-delete-target phrase",
            occurred_at=datetime.now(timezone.utc),
        ),
    )
    db.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="ok",
            meta_json={"kind": "final"},
        )
    )
    close_session(db, session_row, reason="manual", enqueue_close_job=False)
    db.commit()
    run_archive_pipeline(db, config=cfg, session_row=session_row)
    db.commit()
    session_id = session_row.id
    db.close()

    delete_resp = test_client.delete(
        f"/api/assistant/v1/memories/sessions/{session_id}",
        params={"team_id": TEAM_A, "user_id": "user-a"},
        headers=headers,
    )
    assert delete_resp.status_code == 200

    search_resp = test_client.get(
        "/api/assistant/v1/memories/search",
        params={"team_id": TEAM_A, "user_id": "user-a", "query": "cascade-delete"},
        headers=headers,
    )
    assert search_resp.json()["fragments"] == []


def test_web_search_failure_degrades_gracefully(caplog):
    """Tavily 失败时返回可审计错误，日志不含密钥。"""
    caplog.set_level(logging.INFO)
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    team, _ = make_team_repo(db)
    member = Member(
        team_id=team.id,
        dingtalk_user_id="u-web",
        display_name="Web",
        status="active",
    )
    db.add(member)
    db.flush()

    web = WebSearchConfig(
        enabled=True,
        provider="tavily",
        api_key="tvly-secret-key",
        search_url="https://api.tavily.com/search",
        timeout_seconds=10.0,
        max_results=5,
        fetch_max_bytes=1024,
        fetch_max_redirects=3,
    )
    config = load_config("config.yaml").model_copy(update={"web_search": web})

    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.TimeoutException("slow")

    with patch("pulse.capabilities.web.tavily.httpx.Client") as Client:
        Client.return_value.__enter__.return_value = mock_client
        Client.return_value.__exit__.return_value = False
        from assistant_platform.contracts.provider import CapabilityInvokeRequest

        result = invoke_capability(
            db,
            request=CapabilityInvokeRequest(
                invocation_id="inv-1",
                idempotency_key="idem-1",
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.search",
                capability_version="1",
                arguments={"query": "sensitive user query"},
            ),
            config=config,
        )

    assert result.status == "failed"
    assert result.error_code == "provider_timeout"
    assert "tvly-secret-key" not in (result.user_message or "")
    assert "tvly-secret-key" not in str(result.result or "")

    search_logs = [r.message for r in caplog.records if "event=web_search" in r.message]
    assert search_logs
    assert "sensitive user query" not in " ".join(search_logs)
    assert "tvly-secret-key" not in " ".join(search_logs)
    db.close()


def test_archive_pipeline_logs_stage_timing(caplog):
    """归档各阶段完成时记录耗时与状态。"""
    caplog.set_level(logging.INFO)
    Session = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = Session()
    row = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    db.add(row)
    db.add(_msg(row.id, "user", "timing log test", offset=1))
    db.add(_msg(row.id, "assistant", "ok", kind="final", offset=2))
    db.commit()

    archive = run_archive_pipeline(db, config=_assistant_config(), session_row=row)
    assert archive.status == ArchivePipelineStatus.READY.value

    stage_logs = [r.message for r in caplog.records if "event=archive_stage" in r.message]
    assert stage_logs
    assert any("duration_ms=" in msg for msg in stage_logs)
    assert "timing log test" not in " ".join(stage_logs)
    db.close()


def test_observability_logs_exclude_body_text(caplog):
    """可观测日志不含正文或密钥。"""
    caplog.set_level(logging.INFO)
    Session = init_assistant_db("sqlite://", team_id=TEAM_A)
    db = Session()
    secret_phrase = "super-secret-memory-body-xyz"
    closed = _session_row(status="closed", closed_at=datetime.now(timezone.utc))
    db.add(closed)
    db.add(_msg(closed.id, "user", secret_phrase, offset=1))
    db.add(_msg(closed.id, "assistant", "ok", kind="final", offset=2))
    db.commit()
    _close_and_archive(db, closed)

    scope = resolve_search_scope(
        team_id=TEAM_A,
        subject_id="user-a",
        conversation_type="private",
        conversation_id="user-a",
        user_id="user-a",
    )
    build_recall_bundle(
        db,
        query=secret_phrase,
        scope=scope,
        config=_memory_config(),
        visibility_context=VisibilityContext.private("user-a"),
    )
    service = MemoryToolService(
        db,
        config=_memory_config(),
        scope=scope,
        visibility_context=VisibilityContext.private("user-a"),
    )
    service.search(secret_phrase)

    all_logs = " ".join(r.message for r in caplog.records)
    assert secret_phrase not in all_logs
    db.close()
