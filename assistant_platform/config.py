from __future__ import annotations

import os

from pydantic import BaseModel, Field

from assistant_platform.domain.identity import DEFAULT_ASSISTANT_ID


class MemoryChunkingConfig(BaseModel):
    max_tokens_per_chunk: int = 512
    overlap_tokens: int = 64


class MemoryEmbeddingConfig(BaseModel):
    enabled: bool = True
    model: str = "text-embedding-3-small"
    batch_size: int = 32
    dedupe_by_content_hash: bool = True


class MemoryRecallBudgetConfig(BaseModel):
    fragment_top_k: int = 3
    fact_top_k: int = 5
    max_fragments_per_session: int = 2
    context_token_budget: int = 1500
    expand_neighbor_count: int = 2
    timeout_ms: int = 500
    fts_weight: float = 0.5
    vector_weight: float = 0.5


class MemoryBackfillConfig(BaseModel):
    enabled: bool = False
    batch_size: int = 20


class MemoryArchiveConfig(BaseModel):
    enabled: bool = False
    index_version: int = 2
    ledger_retention_days: int = 180


class MemoryFeatureFlags(BaseModel):
    archive_pipeline: bool = False
    auto_recall_per_turn: bool = False
    distill_on_close: bool = False
    profile_compile: bool = False
    backfill: bool = False


class AssistantChatMemoryConfig(BaseModel):
    """Archive, chunking, embedding, recall budget, backfill and feature flags.

    Tavily API keys live in Pulse config only — never store web-search secrets here.
    """

    archive: MemoryArchiveConfig = Field(default_factory=MemoryArchiveConfig)
    chunking: MemoryChunkingConfig = Field(default_factory=MemoryChunkingConfig)
    embedding: MemoryEmbeddingConfig = Field(default_factory=MemoryEmbeddingConfig)
    recall: MemoryRecallBudgetConfig = Field(default_factory=MemoryRecallBudgetConfig)
    backfill: MemoryBackfillConfig = Field(default_factory=MemoryBackfillConfig)
    features: MemoryFeatureFlags = Field(default_factory=MemoryFeatureFlags)


class AssistantLlmConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: float = 30.0
    agent_max_tool_rounds: int = 20
    agent_history_max_messages: int = 40
    agent_total_timeout_seconds: float = 120.0
    agent_max_interim_replies: int = 3
    turn_timeout_seconds: int = 300
    inbox_max_per_drain: int = 5
    job_worker_count: int = 1
    job_processing_timeout_seconds: int = 600


class SkillsVectorConfig(BaseModel):
    """Skill 向量路由：控制每轮名片检索的开关与阈值。

    HashingEmbedder（测试/本地）与生产 OpenAI embedder 的分值分布不同，
    ``score_threshold`` 需按 embedder 分别校准；此处默认偏低以适配 hashing。
    """

    enabled: bool = True
    score_threshold: float = 0.15
    top_k: int = 3
    resync_interval_seconds: int = 60


class AssistantConfig(BaseModel):
    assistant_id: str = DEFAULT_ASSISTANT_ID
    team_id: str = ""  # 运行时由 Pulse team.id 注入或配置
    team_slug: str = "default"
    database_url: str = "sqlite:///data/assistant.db"
    host: str = "127.0.0.1"
    port: int = 8090
    # 服务间调用共享密钥（Pulse mirror → Assistant API）
    service_token: str = ""
    # Pulse internal Provider API（Assistant → Pulse）
    pulse_base_url: str = "http://127.0.0.1:8080"
    pulse_internal_token: str = ""
    # Secret Store 加密密钥；生产必须独立配置，勿复用 service_token
    secret_key: str = ""
    # 已废弃：legacy 记忆库 URL（personamem 时代遗留）。assistant_platform 记忆
    # 现在完全落在 database_url 指向的库里，此字段仅保留以兼容旧环境变量，不再被读取。
    memory_database_url: str | None = None
    # 已废弃：请改用 chat_memory.features / chat_memory.archive.enabled 作为唯一开关。
    # 保留此字段仅为兼容旧环境变量 ASSISTANT_MEMORY_ENABLED=true；默认关闭。
    memory_enabled: bool = False
    # False 时跳过 pulse.db team_settings 覆盖（单测与显式配置场景）
    apply_team_settings_overrides: bool = True
    chat_memory: AssistantChatMemoryConfig = Field(default_factory=AssistantChatMemoryConfig)
    llm: AssistantLlmConfig = Field(default_factory=AssistantLlmConfig)
    skills_enabled: bool = True
    skills_vector: SkillsVectorConfig = Field(default_factory=SkillsVectorConfig)


def load_assistant_config() -> AssistantConfig:
    _load_dotenv_if_present()
    team_id = os.environ.get("ASSISTANT_TEAM_ID", "").strip()
    team_slug = os.environ.get("PULSE_TEAM_SLUG", "default").strip() or "default"
    if not team_id:
        team_id = _resolve_team_id_from_pulse(team_slug)
    cfg = AssistantConfig(
        assistant_id=os.environ.get("ASSISTANT_ID", DEFAULT_ASSISTANT_ID),
        team_id=team_id,
        team_slug=team_slug,
        database_url=os.environ.get("ASSISTANT_DATABASE_URL", "sqlite:///data/assistant.db"),
        host=os.environ.get("ASSISTANT_HOST", "127.0.0.1"),
        port=int(os.environ.get("ASSISTANT_PORT", "8090")),
        service_token=os.environ.get("ASSISTANT_SERVICE_TOKEN", ""),
        pulse_base_url=os.environ.get("PULSE_BASE_URL", "http://127.0.0.1:8080"),
        pulse_internal_token=os.environ.get("PULSE_INTERNAL_TOKEN", ""),
        secret_key=os.environ.get("ASSISTANT_SECRET_KEY", ""),
        memory_database_url=_load_memory_database_url(),
        memory_enabled=os.environ.get("ASSISTANT_MEMORY_ENABLED", "false").lower()
        in ("1", "true", "yes", "on"),
        chat_memory=_load_chat_memory_config(),
        llm=AssistantLlmConfig(
            enabled=os.environ.get("ASSISTANT_LLM_ENABLED", "false").lower()
            in ("1", "true", "yes", "on"),
            api_key=os.environ.get("ASSISTANT_LLM_API_KEY", "").strip(),
            base_url=os.environ.get(
                "ASSISTANT_LLM_BASE_URL", "https://api.openai.com/v1"
            ).strip(),
            model=os.environ.get("ASSISTANT_LLM_MODEL", "").strip(),
            agent_max_tool_rounds=int(
                os.environ.get("ASSISTANT_AGENT_MAX_TOOL_ROUNDS", "20")
            ),
            agent_history_max_messages=int(
                os.environ.get("ASSISTANT_AGENT_HISTORY_MAX_MESSAGES", "40")
            ),
            agent_total_timeout_seconds=float(
                os.environ.get("ASSISTANT_AGENT_TOTAL_TIMEOUT_SECONDS", "120")
            ),
            agent_max_interim_replies=int(
                os.environ.get("ASSISTANT_AGENT_MAX_INTERIM_REPLIES", "3")
            ),
            turn_timeout_seconds=int(
                os.environ.get("ASSISTANT_TURN_TIMEOUT_SECONDS", "300")
            ),
            inbox_max_per_drain=int(
                os.environ.get("ASSISTANT_INBOX_MAX_PER_DRAIN", "5")
            ),
            job_worker_count=int(os.environ.get("ASSISTANT_JOB_WORKER_COUNT", "1")),
            job_processing_timeout_seconds=int(
                os.environ.get("ASSISTANT_JOB_PROCESSING_TIMEOUT_SECONDS", "600")
            ),
        ),
    )
    return _apply_team_assistant_llm_overrides(cfg)


def _apply_team_assistant_llm_overrides(config: AssistantConfig) -> AssistantConfig:
    chat_memory = _apply_team_chat_memory_overrides(config)
    if not config.apply_team_settings_overrides:
        return config.model_copy(update={"chat_memory": chat_memory})
    try:
        from pulse.team_settings_loader import read_team_setting_section

        overrides = read_team_setting_section(
            team_slug=config.team_slug,
            section="assistant_llm",
        )
    except Exception:
        return config.model_copy(update={"chat_memory": chat_memory})
    if not overrides:
        return config.model_copy(update={"chat_memory": chat_memory})

    llm_data = config.llm.model_dump()
    for key in (
        "enabled",
        "base_url",
        "model",
        "api_key",
        "agent_max_tool_rounds",
        "agent_history_max_messages",
        "agent_total_timeout_seconds",
        "agent_max_interim_replies",
        "turn_timeout_seconds",
        "inbox_max_per_drain",
        "job_worker_count",
        "job_processing_timeout_seconds",
    ):
        if key in overrides and overrides[key] not in (None, ""):
            llm_data[key] = overrides[key]
    memory_enabled = config.memory_enabled
    if "memory_enabled" in overrides:
        memory_enabled = bool(overrides["memory_enabled"])
        if not memory_enabled:
            # Legacy assistant_llm.memory_enabled=false → treat as chat memory master off.
            chat_memory = chat_memory.model_copy(
                update={
                    "archive": chat_memory.archive.model_copy(update={"enabled": False}),
                    "features": MemoryFeatureFlags(),
                }
            )
    return config.model_copy(
        update={
            "llm": AssistantLlmConfig(**llm_data),
            "memory_enabled": memory_enabled,
            "chat_memory": chat_memory,
        }
    )


def _apply_team_chat_memory_overrides(config: AssistantConfig) -> AssistantChatMemoryConfig:
    """Merge env defaults with team_settings chat_memory (and legacy assistant_llm.chat_memory)."""
    chat_memory = config.chat_memory
    if not config.apply_team_settings_overrides:
        return chat_memory
    try:
        from pulse.team_settings_loader import read_team_setting_section

        llm_overrides = read_team_setting_section(
            team_slug=config.team_slug,
            section="assistant_llm",
        )
        dedicated = read_team_setting_section(
            team_slug=config.team_slug,
            section="chat_memory",
        )
    except Exception:
        return chat_memory

    nested = llm_overrides.get("chat_memory") if llm_overrides else None
    chat_memory = _apply_chat_memory_overrides(chat_memory, nested)
    chat_memory = _apply_chat_memory_overrides(chat_memory, dedicated)
    return chat_memory


def resolve_effective_llm(config: AssistantConfig) -> AssistantLlmConfig:
    return _apply_team_assistant_llm_overrides(config).llm


def resolve_effective_memory_enabled(config: AssistantConfig) -> bool:
    """True when chat memory is active (archive or any feature flag), with legacy fallback."""
    cfg = _apply_team_assistant_llm_overrides(config)
    chat_memory = cfg.chat_memory
    if chat_memory.archive.enabled:
        return True
    features = chat_memory.features
    if any(
        (
            features.archive_pipeline,
            features.auto_recall_per_turn,
            features.distill_on_close,
            features.profile_compile,
            features.backfill,
        )
    ):
        return True
    return cfg.memory_enabled


def resolve_effective_chat_memory(config: AssistantConfig) -> AssistantChatMemoryConfig:
    return _apply_team_chat_memory_overrides(config)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def _env_bool_with_legacy(
    *,
    canonical: str,
    legacy: str,
    default: bool,
) -> bool:
    """Read a bool env var; prefer canonical ``FEATURES_*`` over legacy ``FEATURE_*``."""
    if canonical in os.environ:
        return _env_bool(canonical, default)
    if legacy in os.environ:
        return _env_bool(legacy, default)
    return default


def _load_chat_memory_config() -> AssistantChatMemoryConfig:
    return AssistantChatMemoryConfig(
        archive=MemoryArchiveConfig(
            enabled=_env_bool("ASSISTANT_CHAT_MEMORY_ARCHIVE_ENABLED", False),
            index_version=int(os.environ.get("ASSISTANT_CHAT_MEMORY_ARCHIVE_INDEX_VERSION", "2")),
            ledger_retention_days=int(
                os.environ.get("ASSISTANT_CHAT_MEMORY_LEDGER_RETENTION_DAYS", "180")
            ),
        ),
        chunking=MemoryChunkingConfig(
            max_tokens_per_chunk=int(
                os.environ.get("ASSISTANT_CHAT_MEMORY_CHUNK_MAX_TOKENS", "512")
            ),
            overlap_tokens=int(os.environ.get("ASSISTANT_CHAT_MEMORY_CHUNK_OVERLAP_TOKENS", "64")),
        ),
        embedding=MemoryEmbeddingConfig(
            enabled=_env_bool("ASSISTANT_CHAT_MEMORY_EMBEDDING_ENABLED", True),
            model=os.environ.get(
                "ASSISTANT_CHAT_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"
            ).strip(),
            batch_size=int(os.environ.get("ASSISTANT_CHAT_MEMORY_EMBEDDING_BATCH_SIZE", "32")),
            dedupe_by_content_hash=_env_bool(
                "ASSISTANT_CHAT_MEMORY_EMBEDDING_DEDUPE_BY_HASH", True
            ),
        ),
        recall=MemoryRecallBudgetConfig(
            fragment_top_k=int(os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_FRAGMENT_TOP_K", "3")),
            fact_top_k=int(os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_FACT_TOP_K", "5")),
            max_fragments_per_session=int(
                os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_MAX_FRAGMENTS_PER_SESSION", "2")
            ),
            context_token_budget=int(
                os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_CONTEXT_TOKEN_BUDGET", "1500")
            ),
            expand_neighbor_count=int(
                os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_EXPAND_NEIGHBOR_COUNT", "2")
            ),
            timeout_ms=int(os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_TIMEOUT_MS", "500")),
            fts_weight=float(os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_FTS_WEIGHT", "0.5")),
            vector_weight=float(os.environ.get("ASSISTANT_CHAT_MEMORY_RECALL_VECTOR_WEIGHT", "0.5")),
        ),
        backfill=MemoryBackfillConfig(
            enabled=_env_bool("ASSISTANT_CHAT_MEMORY_BACKFILL_ENABLED", False),
            batch_size=int(os.environ.get("ASSISTANT_CHAT_MEMORY_BACKFILL_BATCH_SIZE", "20")),
        ),
        features=MemoryFeatureFlags(
            archive_pipeline=_env_bool_with_legacy(
                canonical="ASSISTANT_CHAT_MEMORY_FEATURES_ARCHIVE_PIPELINE",
                legacy="ASSISTANT_CHAT_MEMORY_FEATURE_ARCHIVE_PIPELINE",
                default=False,
            ),
            auto_recall_per_turn=_env_bool_with_legacy(
                canonical="ASSISTANT_CHAT_MEMORY_FEATURES_AUTO_RECALL_PER_TURN",
                legacy="ASSISTANT_CHAT_MEMORY_FEATURE_AUTO_RECALL_PER_TURN",
                default=False,
            ),
            distill_on_close=_env_bool_with_legacy(
                canonical="ASSISTANT_CHAT_MEMORY_FEATURES_DISTILL_ON_CLOSE",
                legacy="ASSISTANT_CHAT_MEMORY_FEATURE_DISTILL_ON_CLOSE",
                default=False,
            ),
            profile_compile=_env_bool_with_legacy(
                canonical="ASSISTANT_CHAT_MEMORY_FEATURES_PROFILE_COMPILE",
                legacy="ASSISTANT_CHAT_MEMORY_FEATURE_PROFILE_COMPILE",
                default=False,
            ),
            backfill=_env_bool_with_legacy(
                canonical="ASSISTANT_CHAT_MEMORY_FEATURES_BACKFILL",
                legacy="ASSISTANT_CHAT_MEMORY_FEATURE_BACKFILL",
                default=False,
            ),
        ),
    )


def _apply_chat_memory_overrides(
    base: AssistantChatMemoryConfig,
    overrides: dict | None,
) -> AssistantChatMemoryConfig:
    if not overrides:
        return base
    data = base.model_dump()
    for section in ("archive", "chunking", "embedding", "recall", "backfill", "features"):
        section_overrides = overrides.get(section)
        if not isinstance(section_overrides, dict):
            continue
        merged = {**data[section], **section_overrides}
        data[section] = merged
    return AssistantChatMemoryConfig.model_validate(data)


def _resolve_team_id_from_pulse(team_slug: str) -> str:
    """Best-effort: read Pulse SQLite team id by slug when ASSISTANT_TEAM_ID is unset."""
    try:
        import sqlite3
        from pathlib import Path

        candidates = [
            Path.cwd() / "data" / "pulse.db",
            Path(__file__).resolve().parents[1] / "data" / "pulse.db",
        ]
        for db_path in candidates:
            if not db_path.is_file():
                continue
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT id FROM teams WHERE slug = ? LIMIT 1",
                    (team_slug,),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
            finally:
                conn.close()
    except Exception:
        return ""
    return ""


def _load_dotenv_if_present() -> None:
    """Load project .env into os.environ.

    Prefer Docker-mounted ``/app/.env`` with override=True so ``compose restart``
    picks up edits (Compose ``env_file`` alone only applies at container create).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    from pathlib import Path

    docker_env = Path("/app/.env")
    if docker_env.is_file():
        load_dotenv(docker_env, override=True)
        return
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            return


def _load_memory_database_url() -> str | None:
    raw = os.environ.get("ASSISTANT_MEMORY_DATABASE_URL")
    if raw is None:
        return None
    import logging

    logging.getLogger(__name__).warning(
        "ASSISTANT_MEMORY_DATABASE_URL is deprecated and ignored; "
        "assistant memory uses ASSISTANT_DATABASE_URL / database_url only"
    )
    return raw.strip()


def validate_runtime_config(config: AssistantConfig, *, strict: bool = False) -> None:
    """Ensure required secrets are configured before serving traffic."""
    missing: list[str] = []
    if not (config.service_token or "").strip():
        missing.append("ASSISTANT_SERVICE_TOKEN")
    if strict:
        if not (config.secret_key or "").strip():
            missing.append("ASSISTANT_SECRET_KEY")
        if config.llm.enabled and not (config.llm.api_key or "").strip():
            missing.append("ASSISTANT_LLM_API_KEY")
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Assistant Platform missing required configuration: {joined}")
