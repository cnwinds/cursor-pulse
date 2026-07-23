"""Build token-budgeted RecallBundle for per-turn injection and tools."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from assistant_platform.config import AssistantChatMemoryConfig
from assistant_platform.memory.archive_indexer import estimate_tokens
from assistant_platform.memory.archive_search import (
    RecallTimeoutError,
    SearchScope,
    hybrid_search,
    recall_fact_items,
)
from assistant_platform.memory.contracts import (
    ArchiveHit,
    FactRecallItem,
    ProfileGuidance,
    RecallBundle,
    RecallCursor,
    SearchPageMeta,
)
from assistant_platform.memory.observability import log_recall_bundle
from assistant_platform.profiles.compiler import compile_profile_guidance
from assistant_platform.memory.semantic.domain import VisibilityContext
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository

logger = logging.getLogger(__name__)


def _estimate_bundle_tokens(
    fragments: tuple[ArchiveHit, ...],
    facts: tuple[FactRecallItem, ...],
    profile: ProfileGuidance | None,
) -> int:
    total = 0
    for hit in fragments:
        total += estimate_tokens(hit.text) + 24
    for fact in facts:
        total += estimate_tokens(fact.content) + 12
    if profile is not None:
        for item in profile.items:
            total += estimate_tokens(item.guidance) + 8
    return total


def _trim_to_budget(
    fragments: list[ArchiveHit],
    facts: list[FactRecallItem],
    profile: ProfileGuidance | None,
    *,
    token_budget: int,
) -> tuple[tuple[ArchiveHit, ...], tuple[FactRecallItem, ...], ProfileGuidance | None, int]:
    frags = list(fragments)
    fact_list = list(facts)
    prof = profile
    while True:
        estimate = _estimate_bundle_tokens(tuple(frags), tuple(fact_list), prof)
        if estimate <= token_budget:
            return tuple(frags), tuple(fact_list), prof, estimate
        if frags:
            frags.pop()
            continue
        if fact_list:
            fact_list.pop()
            continue
        if prof is not None:
            prof = None
            continue
        return (), (), None, 0


def build_recall_bundle(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    config: AssistantChatMemoryConfig,
    visibility_context: VisibilityContext,
    include_profile: bool = True,
    memory_repo: SemanticMemoryRepository | None = None,
    cursor: RecallCursor | None = None,
    llm_api_key: str = "",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_timeout_seconds: float = 30.0,
    llm_enabled: bool = False,
) -> RecallBundle:
    recall = config.recall
    sources: list[str] = []
    degraded = False
    degrade_reason: str | None = None

    try:
        fragments, page = hybrid_search(
            session,
            query=query,
            scope=scope,
            config=config,
            cursor=cursor,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_timeout_seconds=llm_timeout_seconds,
            llm_enabled=llm_enabled,
        )
        if fragments:
            sources.append("fts")
            sources.append("vector")
    except RecallTimeoutError:
        logger.warning("hybrid_search timed out after %sms", recall.timeout_ms)
        fragments = ()
        page = SearchPageMeta(total_hits=0, returned_count=0, has_more=False)
        degraded = True
        degrade_reason = "recall_timeout"
    except Exception as exc:
        logger.exception("hybrid_search failed")
        fragments = ()
        page = SearchPageMeta(total_hits=0, returned_count=0, has_more=False)
        degraded = True
        degrade_reason = str(exc)[:120]

    facts: tuple[FactRecallItem, ...] = ()
    try:
        fact_items = recall_fact_items(
            session,
            query=query,
            scope=scope,
            visibility_context=visibility_context,
            top_k=recall.fact_top_k,
            memory_repo=memory_repo,
        )
        if fact_items:
            sources.append("facts")
        facts = tuple(fact_items)
    except Exception as exc:
        logger.exception("fact recall failed")
        degraded = True
        degrade_reason = degrade_reason or str(exc)[:120]

    profile: ProfileGuidance | None = None
    if include_profile and config.features.profile_compile and scope.conversation_type == "private":
        try:
            profile = compile_profile_guidance(
                session,
                user_id=scope.subject_id,
                team_id=scope.team_id,
            )
            if profile.items:
                sources.append("profile")
        except Exception:
            logger.exception("profile compile failed during recall")

    trimmed_frags, trimmed_facts, trimmed_profile, token_estimate = _trim_to_budget(
        list(fragments),
        list(facts),
        profile if profile and profile.items else None,
        token_budget=recall.context_token_budget,
    )

    bundle = RecallBundle(
        fragments=trimmed_frags,
        facts=trimmed_facts,
        profile=trimmed_profile,
        page=page,
        token_estimate=token_estimate,
        recall_sources=tuple(dict.fromkeys(sources)),
        built_at=datetime.now(timezone.utc),
        degraded=degraded,
        degrade_reason=degrade_reason,
    )
    log_recall_bundle(
        team_id=scope.team_id,
        subject_id=scope.subject_id,
        scope=scope.scope.value,
        sources=bundle.recall_sources,
        fragment_count=len(bundle.fragments),
        fact_count=len(bundle.facts),
        profile_count=len(bundle.profile.items) if bundle.profile else 0,
        token_estimate=bundle.token_estimate,
        total_hits=bundle.page.total_hits,
        degraded=bundle.degraded,
        degrade_reason=bundle.degrade_reason,
    )
    return bundle


def format_recall_block(bundle: RecallBundle) -> str:
    """Render a low-priority memory section for system prompt injection."""
    if bundle.degraded and not bundle.fragments and not bundle.facts and not bundle.profile:
        return ""

    lines = [
        "## 历史记忆（低优先级参考）",
        "以下片段来自已关闭会话的脱敏归档；若与当前用户消息冲突，以当前消息为准。",
        "不得向用户暗示「我记得一切」；需要更多上下文时使用 memory_search / memory_expand 等工具。",
        "",
    ]
    if bundle.fragments:
        lines.append("### 相关片段")
        for hit in bundle.fragments:
            lines.append(
                f"- [rank={hit.rank} session={hit.session_id} seq={hit.start_seq}-{hit.end_seq} "
                f"total_msgs={hit.session_message_total}] {hit.text[:400]}"
            )
        lines.append("")
    if bundle.facts:
        lines.append("### 稳定事实")
        for fact in bundle.facts:
            lines.append(f"- ({fact.source_type.value}, conf={fact.confidence:.2f}) {fact.content[:240]}")
        lines.append("")
    if bundle.profile and bundle.profile.items:
        lines.append("### 交互偏好")
        for item in bundle.profile.items:
            lines.append(f"- {item.dimension.value}: {item.guidance}")
        lines.append("")
    if bundle.page.total_hits:
        lines.append(
            f"（召回 {bundle.page.returned_count}/{bundle.page.total_hits} 片段，"
            f"约 {bundle.token_estimate} tokens）"
        )
    return "\n".join(lines).strip()
