from __future__ import annotations

from datetime import datetime, timezone

from assistant_platform.config import (
    AssistantChatMemoryConfig,
    AssistantConfig,
    MemoryArchiveConfig,
    MemoryFeatureFlags,
    MemoryRecallBudgetConfig,
    load_assistant_config,
    resolve_effective_chat_memory,
    resolve_effective_memory_enabled,
)
from assistant_platform.memory.archive_pipeline import should_run_archive_pipeline
from assistant_platform.memory import (
    ArchiveHit,
    ChunkAnchor,
    MemoryScope,
    MemorySourceType,
    RecallBundle,
    RecallCursor,
    SearchPageMeta,
)


def test_chat_memory_config_defaults():
    cfg = AssistantConfig()
    assert cfg.memory_enabled is False
    chat = cfg.chat_memory
    assert chat.archive.enabled is False
    assert chat.archive.index_version == 2
    assert chat.archive.ledger_retention_days == 180
    assert chat.chunking.max_tokens_per_chunk == 512
    assert chat.embedding.model == "text-embedding-3-small"
    assert chat.recall.fragment_top_k == 3
    assert chat.recall.context_token_budget == 1500
    assert chat.features.auto_recall_per_turn is False


def test_recall_budget_weights_sum_sensible():
    recall = MemoryRecallBudgetConfig()
    assert 0.0 <= recall.fts_weight <= 1.0
    assert 0.0 <= recall.vector_weight <= 1.0


def test_resolve_effective_chat_memory_returns_copy():
    cfg = AssistantConfig()
    resolved = resolve_effective_chat_memory(cfg)
    assert isinstance(resolved, AssistantChatMemoryConfig)


def test_feature_flags_prefer_features_env_over_legacy(monkeypatch):
    monkeypatch.setenv("ASSISTANT_CHAT_MEMORY_FEATURES_AUTO_RECALL_PER_TURN", "true")
    monkeypatch.setenv("ASSISTANT_CHAT_MEMORY_FEATURE_AUTO_RECALL_PER_TURN", "false")
    cfg = load_assistant_config()
    assert cfg.chat_memory.features.auto_recall_per_turn is True


def test_feature_flags_legacy_env_still_supported(monkeypatch):
    monkeypatch.delenv("ASSISTANT_CHAT_MEMORY_FEATURES_PROFILE_COMPILE", raising=False)
    monkeypatch.setenv("ASSISTANT_CHAT_MEMORY_FEATURE_PROFILE_COMPILE", "true")
    cfg = load_assistant_config()
    assert cfg.chat_memory.features.profile_compile is True


def test_resolve_effective_memory_enabled_from_chat_memory(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.config._apply_team_assistant_llm_overrides",
        lambda config: config,
    )
    cfg = AssistantConfig(
        memory_enabled=False,
        chat_memory=AssistantChatMemoryConfig(
            archive=MemoryArchiveConfig(enabled=True),
        ),
    )
    assert resolve_effective_memory_enabled(cfg) is True


def test_legacy_memory_enabled_false_disables_archive(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.config._apply_team_assistant_llm_overrides",
        lambda config: config,
    )
    cfg_off = AssistantConfig(
        memory_enabled=False,
        chat_memory=AssistantChatMemoryConfig(
            archive=MemoryArchiveConfig(enabled=False),
        ),
    )
    assert resolve_effective_memory_enabled(cfg_off) is False


def test_legacy_memory_enabled_true_compat(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.config._apply_team_assistant_llm_overrides",
        lambda config: config,
    )
    cfg = AssistantConfig(memory_enabled=True)
    assert resolve_effective_memory_enabled(cfg) is True


def test_should_run_archive_pipeline_default_off(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.config._apply_team_chat_memory_overrides",
        lambda config: config.chat_memory,
    )
    assert should_run_archive_pipeline(AssistantConfig()) is False


def test_should_run_archive_pipeline_requires_explicit_flags(monkeypatch):
    monkeypatch.setattr(
        "assistant_platform.config._apply_team_chat_memory_overrides",
        lambda config: config.chat_memory,
    )
    legacy_only = AssistantConfig(memory_enabled=True)
    assert should_run_archive_pipeline(legacy_only) is False

    with_archive = AssistantConfig(
        chat_memory=AssistantChatMemoryConfig(
            archive=MemoryArchiveConfig(enabled=True),
        ),
    )
    assert should_run_archive_pipeline(with_archive) is True

    with_pipeline = AssistantConfig(
        chat_memory=AssistantChatMemoryConfig(
            features=MemoryFeatureFlags(archive_pipeline=True),
        ),
    )
    assert should_run_archive_pipeline(with_pipeline) is True


def test_archive_hit_is_frozen():
    now = datetime.now(timezone.utc)
    anchor = ChunkAnchor(session_id="s1", chunk_index=0, start_seq=1, end_seq=2)
    hit = ArchiveHit(
        memory_id="m1",
        session_id="s1",
        source_type=MemorySourceType.ARCHIVE_CHUNK,
        scope=MemoryScope.PERSONAL,
        text="hello",
        occurred_from=now,
        occurred_to=now,
        start_seq=1,
        end_seq=2,
        chunk_index=0,
        session_message_total=10,
        session_chunk_total=3,
        rank=1,
        score=0.9,
        anchor=anchor,
    )
    bundle = RecallBundle(
        fragments=(hit,),
        page=SearchPageMeta(
            total_hits=1,
            returned_count=1,
            has_more=False,
            cursor=RecallCursor(query_fingerprint="q", sort_key="0.9:m1", offset=1),
        ),
    )
    assert bundle.fragments[0].memory_id == "m1"
    assert bundle.page.cursor is not None
    assert bundle.page.cursor.offset == 1
