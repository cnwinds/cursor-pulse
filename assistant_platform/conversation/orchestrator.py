from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from assistant_platform.capabilities.executor import CapabilityExecutor
from assistant_platform.capabilities.pulse_client import PulseCapabilityClient
from assistant_platform.capabilities.resolve import resolve_capabilities
from assistant_platform.config import AssistantConfig, resolve_effective_chat_memory, resolve_effective_llm
from assistant_platform.conversation.agent_policy import build_agent_system
from assistant_platform.conversation.agent_runtime import AgentRuntime, AgentUnavailable
from assistant_platform.conversation.agent_tools import (
    TOOL_EXCLUSIONS,
    tool_name_for_capability,
)
from assistant_platform.conversation.agent_trace import persist_agent_trace_event
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.responder import simple_reply
from assistant_platform.conversation.session_history import load_session_history_messages
from assistant_platform.conversation.subject import resolve_subject_id
from assistant_platform.conversation.turn_inbox import (
    TurnInbox,
    end_turn,
    try_schedule_next_turn,
)
from assistant_platform.evolution.clustering import cluster_low_score_reviews
from assistant_platform.llm import build_assistant_llm_client
from assistant_platform.memory.archive_pipeline import run_archive_pipeline, should_run_archive_pipeline
from assistant_platform.memory.agent_tools import MemoryToolService
from assistant_platform.memory.archive_search import resolve_search_scope
from assistant_platform.memory.context_builder import build_recall_bundle
from assistant_platform.profiles.signals import create_profile_signal_from_session
from assistant_platform.memory.semantic.domain import VisibilityContext
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository
from assistant_platform.prompts.compose import compose_system_supplement
from assistant_platform.conversation.reply_timing import ReplyTurnTimer
from assistant_platform.review.auto_review import run_auto_review
from assistant_platform.integrations.channel_reply import send_channel_reply
from assistant_platform.skills.models import SkillActorContext, SkillDocResult
from assistant_platform.skills.registry import DEFAULT_SKILL_WINDOW_LINES, SkillRegistry
from assistant_platform.skills.vector_sync import build_skill_vector_index
from assistant_platform.storage.models import IncomingEventRow
from assistant_platform.storage.repository import AssistantRepository

logger = logging.getLogger(__name__)

_UNAVAILABLE = "助手暂时不可用，请稍后再试。"


def _persist_and_queue_reply(
    db_session: Session,
    repo: AssistantRepository,
    *,
    session_row: ChatSessionRow,
    reply_endpoint: dict,
    text: str,
    kind: str,
) -> ChatMessageRow:
    assistant_message = ChatMessageRow(
        session_id=session_row.id,
        role="assistant",
        text_redacted=text,
        secret_refs_json=[],
        meta_json={"kind": kind},
    )
    db_session.add(assistant_message)
    db_session.flush()
    reply_payload = {
        "session_id": session_row.id,
        "message_id": assistant_message.id,
        "reply_endpoint": reply_endpoint,
        "text": text,
        "kind": kind,
    }
    repo.add_outbox(
        assistant_id=session_row.assistant_id,
        team_id=session_row.team_id,
        kind="reply.send",
        payload=reply_payload,
    )
    repo.add_job(job_type="reply.send", payload=reply_payload)
    logger.info(
        "reply.timing stage=reply_queued session_id=%s message_id=%s kind=%s "
        "preview=%r",
        session_row.id,
        assistant_message.id,
        kind,
        (text or "")[:80],
    )
    return assistant_message


def _persist_and_deliver_interim(
    db_session: Session,
    repo: AssistantRepository,
    *,
    config: AssistantConfig,
    session_row: ChatSessionRow,
    reply_endpoint: dict,
    text: str,
) -> ChatMessageRow:
    """Persist interim message and deliver immediately (no reply.send job)."""
    assistant_message = ChatMessageRow(
        session_id=session_row.id,
        role="assistant",
        text_redacted=text,
        secret_refs_json=[],
        meta_json={"kind": "interim", "delivered_sync": True},
    )
    db_session.add(assistant_message)
    db_session.flush()
    reply_payload = {
        "session_id": session_row.id,
        "message_id": assistant_message.id,
        "reply_endpoint": reply_endpoint,
        "text": text,
        "kind": "interim",
    }
    repo.add_outbox(
        assistant_id=session_row.assistant_id,
        team_id=session_row.team_id,
        kind="reply.send",
        payload=reply_payload,
    )
    # Commit before the HTTP send so the write lock is released during network
    # I/O and the interim message is durably persisted regardless of send result.
    db_session.commit()
    logger.info(
        "reply.timing stage=interim_deliver_sync session_id=%s message_id=%s preview=%r",
        session_row.id,
        assistant_message.id,
        (text or "")[:80],
    )
    send_channel_reply(reply_payload, config)
    return assistant_message


def _actor_from_incoming(incoming: IncomingEventRow) -> tuple[str, str | None]:
    endpoint = incoming.reply_endpoint_json or {}
    member_id = endpoint.get("member_id") or incoming.sender_channel_user_id
    role = endpoint.get("role")
    if isinstance(role, str) and role.strip():
        return str(member_id), role.strip()
    return str(member_id), None


def _visibility_context(session_row: ChatSessionRow, user_id: str) -> VisibilityContext:
    if session_row.conversation_type == "group":
        return VisibilityContext.public()
    return VisibilityContext.private(user_id)


def _memory_tools_enabled(chat_memory) -> bool:
    return bool(chat_memory.archive.enabled or chat_memory.features.auto_recall_per_turn)


def _turn_context_snapshot(
    *,
    skill_cards,
    skill_previews: dict[str, SkillDocResult] | None,
    capabilities,
    skills_enabled: bool,
    memory_tools_enabled: bool,
) -> dict[str, Any]:
    skills = []
    preview_map = skill_previews or {}
    for card in skill_cards or []:
        item: dict[str, Any] = {
            "skill_id": card.skill_id,
            "name": card.name,
            "summary": card.summary,
        }
        preview = preview_map.get(card.skill_id)
        if preview is not None:
            item.update(
                {
                    "total_lines": preview.total_lines,
                    "loaded_lines": preview.loaded_lines,
                    "start_line": preview.start_line,
                    "end_line": preview.end_line,
                    "has_more": preview.has_more,
                }
            )
        skills.append(item)
    tools: list[dict[str, str]] = []
    for cap in capabilities:
        if cap.key in TOOL_EXCLUSIONS:
            continue
        tools.append(
            {
                "name": tool_name_for_capability(cap.key),
                "capability_key": cap.key,
                "display_name": cap.display_name or cap.key,
            }
        )
    if skills_enabled:
        tools.append(
            {
                "name": "load_skill_docs",
                "capability_key": "",
                "display_name": "加载技能说明书",
            }
        )
    if memory_tools_enabled:
        for name, label in (
            ("memory_search", "历史记忆搜索"),
            ("memory_expand", "历史记忆展开"),
            ("memory_get_session_summary", "会话摘要"),
            ("memory_read_range", "范围读取"),
        ):
            tools.append(
                {"name": name, "capability_key": "", "display_name": label}
            )
    tools.append(
        {
            "name": "notify_user",
            "capability_key": "",
            "display_name": "进度通知",
        }
    )
    return {"type": "context", "skills": skills, "tools": tools}


def _load_skill_previews(
    *,
    registry: SkillRegistry,
    actor: SkillActorContext,
    cards,
) -> dict[str, SkillDocResult]:
    previews: dict[str, SkillDocResult] = {}
    for card in cards or []:
        try:
            previews[card.skill_id] = registry.load_docs(
                card.skill_id,
                actor=actor,
                start_line=1,
                max_lines=DEFAULT_SKILL_WINDOW_LINES,
            )
        except Exception:
            logger.exception(
                "skill preview load failed skill_id=%s", card.skill_id
            )
    return previews


def generate_reply_text(
    db_session: Session,
    *,
    config: AssistantConfig,
    incoming: IncomingEventRow | None,
    text: str,
    session_row: ChatSessionRow | None = None,
    display_name: str = "用户",
    pulse_client: PulseCapabilityClient | None = None,
    turn_inbox: TurnInbox | None = None,
    on_interim_reply: Callable[[str], None] | None = None,
    on_agent_trace: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    if incoming is None:
        return simple_reply(text)

    actor_member_id, role = _actor_from_incoming(incoming)
    subject_id = resolve_subject_id(
        member_id=actor_member_id,
        channel_user_id=incoming.sender_channel_user_id,
    )

    client = build_assistant_llm_client(config)
    if client is None:
        return _UNAVAILABLE

    capabilities = resolve_capabilities(
        db_session,
        team_id=incoming.team_id,
        role=role,
        member_id=actor_member_id,
    )
    llm_cfg = resolve_effective_llm(config)
    chat_memory = resolve_effective_chat_memory(config)
    history: list[dict[str, Any]] = []
    recall_bundle = None
    memory_tools: MemoryToolService | None = None
    conversation_type = session_row.conversation_type if session_row is not None else "private"
    user_id = (incoming.sender_channel_user_id if incoming else None) or (
        session_row.user_id if session_row else subject_id
    )
    if session_row is not None:
        history = load_session_history_messages(
            db_session,
            session_id=session_row.id,
            limit=llm_cfg.agent_history_max_messages,
        )
        if (
            history
            and history[-1].get("role") == "user"
            and history[-1].get("content") == text
        ):
            history = history[:-1]

        if chat_memory.features.auto_recall_per_turn:
            try:
                scope = resolve_search_scope(
                    team_id=incoming.team_id,
                    subject_id=subject_id,
                    conversation_type=conversation_type,
                    conversation_id=session_row.conversation_id,
                    user_id=user_id,
                    exclude_session_id=session_row.id,
                )
                recall_bundle = build_recall_bundle(
                    db_session,
                    query=text,
                    scope=scope,
                    config=chat_memory,
                    visibility_context=_visibility_context(session_row, user_id or subject_id),
                    include_profile=chat_memory.features.profile_compile,
                    memory_repo=SemanticMemoryRepository(db_session),
                    llm_api_key=config.llm.api_key,
                    llm_base_url=config.llm.base_url,
                    llm_timeout_seconds=config.llm.timeout_seconds,
                    llm_enabled=config.llm.enabled,
                )
            except Exception:
                logger.exception("auto recall per turn failed; continuing without injection")

        if _memory_tools_enabled(chat_memory):
            scope = resolve_search_scope(
                team_id=incoming.team_id,
                subject_id=subject_id,
                conversation_type=conversation_type,
                conversation_id=session_row.conversation_id if session_row else subject_id,
                user_id=user_id,
                exclude_session_id=session_row.id if session_row else None,
            )
            memory_tools = MemoryToolService(
                db_session,
                config=chat_memory,
                scope=scope,
                visibility_context=_visibility_context(session_row, user_id or subject_id),
            )

    skill_registry: SkillRegistry | None = None
    skill_actor: SkillActorContext | None = None
    skill_cards = None
    skill_previews: dict[str, SkillDocResult] = {}
    if config.skills_enabled:
        try:
            skill_registry = SkillRegistry()
        except Exception:
            # A bad skill doc (YAML/frontmatter error) must not take down every
            # chat turn — fall back to no skills for this turn and keep going.
            logger.exception("skill registry build failed; continuing without skills")
            skill_registry = None

        if skill_registry is not None:
            skill_actor = SkillActorContext(
                member_id=actor_member_id,
                role=role,
                authorized_capability_keys=frozenset(c.key for c in capabilities),
            )
            skill_cards = []
            if config.skills_vector.enabled:
                skill_vector_index = None
                try:
                    skill_vector_index = build_skill_vector_index(
                        db_session, config, registry=skill_registry
                    )
                except Exception:
                    logger.exception("skill vector index build failed; injecting no cards")
                if skill_vector_index is not None:
                    try:
                        skill_cards = skill_vector_index.route_cards(text, skill_actor)
                    except Exception:
                        logger.exception("skill vector route failed; injecting no cards")
                        skill_cards = []
            else:
                logger.debug(
                    "skills vector disabled; no skill cards injected"
                )
            if skill_cards:
                skill_previews = _load_skill_previews(
                    registry=skill_registry,
                    actor=skill_actor,
                    cards=skill_cards,
                )
        else:
            skill_cards = []

    system = build_agent_system(
        prompt_studio_supplement=compose_system_supplement(),
        capabilities=capabilities,
        subject_id=subject_id,
        conversation_type=conversation_type,
        recall_bundle=recall_bundle,
        memory_tools_enabled=memory_tools is not None,
        skill_cards=skill_cards,
        skill_previews=skill_previews or None,
        skills_enabled=config.skills_enabled,
    )

    if on_agent_trace is not None:
        try:
            on_agent_trace(
                _turn_context_snapshot(
                    skill_cards=skill_cards,
                    skill_previews=skill_previews or None,
                    capabilities=capabilities,
                    skills_enabled=config.skills_enabled and skill_registry is not None,
                    memory_tools_enabled=memory_tools is not None,
                )
            )
        except Exception:
            logger.exception("persist turn context snapshot failed")

    pulse = pulse_client or PulseCapabilityClient(
        base_url=config.pulse_base_url,
        internal_token=config.pulse_internal_token,
    )
    owns = pulse_client is None
    try:
        executor = CapabilityExecutor(
            session=db_session, config=config, pulse_client=pulse
        )
        runtime = AgentRuntime(
            llm=client,
            executor=executor,
            capabilities=list(capabilities),
            max_tool_rounds=llm_cfg.agent_max_tool_rounds,
            max_interim_replies=llm_cfg.agent_max_interim_replies,
            subject_id=subject_id,
            memory_tools=memory_tools,
            skill_registry=skill_registry,
            skill_actor=skill_actor,
            skill_doc_token_budget=chat_memory.recall.context_token_budget,
        )
        return runtime.run(
            system=system,
            history=history,
            user_text=text,
            actor_member_id=actor_member_id,
            team_id=incoming.team_id,
            role=role,
            conversation_type=conversation_type,
            inbox=turn_inbox,
            on_interim_reply=on_interim_reply,
            on_agent_trace=on_agent_trace,
        )
    except AgentUnavailable as exc:
        return str(exc) or _UNAVAILABLE
    finally:
        if owns:
            pulse.close()


def process_session_job(
    db_session: Session,
    payload: dict,
    config: AssistantConfig,
    *,
    pulse_client: PulseCapabilityClient | None = None,
) -> None:
    session_id = payload["session_id"]
    message_id = payload["message_id"]
    incoming_event_id = payload.get("incoming_event_id")

    session_row = db_session.get(ChatSessionRow, session_id)
    if session_row is None:
        raise ValueError(f"session not found: {session_id}")

    user_message = db_session.get(ChatMessageRow, message_id)
    if user_message is None or user_message.session_id != session_id:
        raise ValueError(f"user message not found for session: {message_id}")

    incoming = (
        db_session.get(IncomingEventRow, incoming_event_id) if incoming_event_id else None
    )
    reply_endpoint = incoming.reply_endpoint_json if incoming else {}

    text = user_message.text_redacted or ""
    display_name = incoming.sender_display_name if incoming else "用户"
    inbox = TurnInbox(
        db_session,
        session_row,
        max_per_drain=resolve_effective_llm(config).inbox_max_per_drain,
    )
    repo = AssistantRepository(db_session)
    timer = ReplyTurnTimer(session_id=session_id, trigger_message_id=message_id)
    timer.mark("session_process_start", incoming_event_id=incoming_event_id or "")
    try:
        def emit_interim(text: str) -> None:
            timer.mark("interim_emit", preview=(text or "")[:80])
            _persist_and_deliver_interim(
                db_session,
                repo,
                config=config,
                session_row=session_row,
                reply_endpoint=reply_endpoint,
                text=text,
            )

        def emit_trace(event: dict) -> None:
            persist_agent_trace_event(
                db_session,
                session_row=session_row,
                event=event,
                commit=True,
            )

        timer.mark("agent_run_start")
        reply_text = generate_reply_text(
            db_session,
            config=config,
            incoming=incoming,
            text=text,
            session_row=session_row,
            display_name=display_name,
            pulse_client=pulse_client,
            turn_inbox=inbox,
            on_interim_reply=emit_interim,
            on_agent_trace=emit_trace,
        )
        timer.mark("agent_run_done", reply_preview=(reply_text or "")[:80])

        _persist_and_queue_reply(
            db_session,
            repo,
            session_row=session_row,
            reply_endpoint=reply_endpoint,
            text=reply_text,
            kind="final",
        )
        timer.mark("session_process_done")
    finally:
        end_turn(db_session, session_row)
        db_session.add(session_row)
        db_session.flush()
        try_schedule_next_turn(db_session, session_row, repo)


def process_session_close_job(
    db_session: Session,
    payload: dict,
    config: AssistantConfig,
) -> None:
    session_id = payload["session_id"]
    session_row = db_session.get(ChatSessionRow, session_id)
    if session_row is None or session_row.status != "closed":
        return

    if should_run_archive_pipeline(config):
        run_archive_pipeline(db_session, config=config, session_row=session_row)
    else:
        create_profile_signal_from_session(db_session, session_row)
    review = run_auto_review(db_session, session_id)
    if review.score < 60:
        cluster_low_score_reviews(db_session)
