from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from assistant_platform.capabilities.executor import CapabilityExecutor
from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_tools import (
    NOTIFY_USER_TOOL_NAME,
    VERBATIM_PRIVATE_CAPABILITIES,
    is_builtin_tool,
    is_local_memory_tool,
    resolve_capability_for_tool_name,
    tools_from_capabilities,
)
from assistant_platform.conversation.turn_inbox import TurnInbox
from assistant_platform.memory.agent_tools import MemoryToolService, invoke_memory_tool
from assistant_platform.skills.agent_tools import invoke_load_skill_docs, is_local_skill_tool
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_UNAVAILABLE = "助手暂时不可用，请稍后再试。"
_MAX_ROUNDS_MSG = "这次需要的步骤较多，请把请求拆成更小的几步，我再继续帮你。"
_DEFAULT_MAX_INTERIM_REPLIES = 3
_ACK_NUDGE = (
    "（系统）你刚才只回复了确认语，还没有调用任何 tool。"
    "请立刻调用完成用户请求所需的 tool；确认语必须与 tool_calls 同轮输出，"
    "禁止只说「好的/稍等/来看看」就结束。"
)

InterimReplyCallback = Callable[[str], None]
AgentTraceCallback = Callable[[dict[str, Any]], None]

_ACK_ONLY_RE = re.compile(
    r"(好的|稍等|请稍|马上|这就|来看|我来查|帮你查|看一下|查一下|正在查|处理中)",
)


def _looks_like_bare_ack(content: str) -> bool:
    """True when the model only acknowledged and did not deliver a real answer."""
    text = (content or "").strip()
    if not text or len(text) > 80:
        return False
    if "\n" in text:
        return False
    return bool(_ACK_ONLY_RE.search(text))


class SupportsCompleteWithTools(Protocol):
    def complete_with_tools(
        self, *, messages: list[dict], tools: list[dict], temperature: float = 0.1
    ) -> dict: ...


class AgentUnavailable(Exception):
    pass


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: SupportsCompleteWithTools,
        executor: CapabilityExecutor,
        capabilities: list[ResolvedCapability],
        max_tool_rounds: int = 20,
        max_interim_replies: int = _DEFAULT_MAX_INTERIM_REPLIES,
        subject_id: str,
        memory_tools: MemoryToolService | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_actor: SkillActorContext | None = None,
        skill_doc_token_budget: int = 4000,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._capabilities = list(capabilities)
        self._max_tool_rounds = max(1, max_tool_rounds)
        self._max_interim_replies = max(0, max_interim_replies)
        self._subject_id = subject_id
        self._memory_tools = memory_tools
        self._skill_registry = skill_registry
        self._skill_actor = skill_actor
        self._skill_doc_token_budget = max(256, skill_doc_token_budget)
        skills_enabled = skill_registry is not None and skill_actor is not None
        self._tools = tools_from_capabilities(
            self._capabilities,
            include_memory_tools=memory_tools is not None,
            include_skill_tools=skills_enabled,
        )

    def run(
        self,
        *,
        system: str,
        history: list[dict[str, Any]],
        user_text: str,
        actor_member_id: str,
        team_id: str,
        role: str | None,
        conversation_type: str = "private",
        inbox: TurnInbox | None = None,
        on_interim_reply: InterimReplyCallback | None = None,
        on_agent_trace: AgentTraceCallback | None = None,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        interim_count = 0
        ack_nudge_used = False
        run_t0 = time.monotonic()

        def emit_trace(event: dict[str, Any]) -> None:
            if on_agent_trace is None:
                return
            try:
                on_agent_trace(event)
            except Exception:
                logger.exception(
                    "agent trace callback failed subject=%s type=%s",
                    self._subject_id,
                    event.get("type"),
                )

        def maybe_emit_interim(text: str) -> bool:
            nonlocal interim_count
            cleaned = (text or "").strip()
            if not cleaned or on_interim_reply is None:
                return False
            if interim_count >= self._max_interim_replies:
                return False
            on_interim_reply(cleaned)
            interim_count += 1
            return True

        for round_index in range(self._max_tool_rounds):
            self._inject_inbox_messages(messages, inbox)
            round_no = round_index + 1

            llm_t0 = time.monotonic()
            logger.info(
                "reply.timing stage=llm_round_start subject_id=%s round=%d elapsed_ms=%d",
                self._subject_id,
                round_no,
                int((llm_t0 - run_t0) * 1000),
            )
            try:
                resp = self._llm.complete_with_tools(
                    messages=messages, tools=self._tools
                )
            except Exception as exc:
                logger.exception("agent llm call failed subject=%s", self._subject_id)
                raise AgentUnavailable(_UNAVAILABLE) from exc
            logger.info(
                "reply.timing stage=llm_round_done subject_id=%s round=%d elapsed_ms=%d "
                "has_content=%s tool_calls=%d",
                self._subject_id,
                round_no,
                int((time.monotonic() - run_t0) * 1000),
                bool((resp.get("content") or "").strip()),
                len(resp.get("tool_calls") or []),
            )

            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                content = (resp.get("content") or "").strip()
                # 模型常把「先回应」做成无 tool 的终局回复；对空确认续跑一轮。
                if (
                    content
                    and not ack_nudge_used
                    and _looks_like_bare_ack(content)
                    and round_index + 1 < self._max_tool_rounds
                ):
                    ack_nudge_used = True
                    delivered = maybe_emit_interim(content)
                    emit_trace(
                        {
                            "type": "thinking",
                            "text": content,
                            "round": round_no,
                            "delivered_as_interim": delivered,
                            "ack_nudge": True,
                        }
                    )
                    messages.append(
                        resp.get("raw_assistant_message")
                        or {"role": "assistant", "content": content}
                    )
                    messages.append({"role": "user", "content": _ACK_NUDGE})
                    logger.info(
                        "reply.timing stage=ack_nudge subject_id=%s round=%d preview=%r",
                        self._subject_id,
                        round_no,
                        content[:80],
                    )
                    continue
                return content or _UNAVAILABLE

            content = (resp.get("content") or "").strip()
            reasoning = (resp.get("reasoning") or resp.get("reasoning_content") or "").strip()
            thinking_text = content or reasoning
            delivered = False
            if content:
                delivered = maybe_emit_interim(content)
            if thinking_text:
                emit_trace(
                    {
                        "type": "thinking",
                        "text": thinking_text,
                        "round": round_no,
                        "delivered_as_interim": delivered,
                    }
                )

            raw_assistant = resp.get("raw_assistant_message") or {
                "role": "assistant",
                "content": resp.get("content") or None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc.get("arguments") or "{}",
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            }
            messages.append(raw_assistant)

            tool_payloads: list[str] = []
            for tc in tool_calls:
                name = tc.get("name") or ""
                tool_t0 = time.monotonic()
                logger.info(
                    "reply.timing stage=tool_invoke_start subject_id=%s tool=%s elapsed_ms=%d",
                    self._subject_id,
                    name,
                    int((tool_t0 - run_t0) * 1000),
                )
                if is_builtin_tool(name):
                    payload = self._invoke_builtin(
                        tc,
                        emit_interim=maybe_emit_interim,
                    )
                elif (
                    is_local_skill_tool(name)
                    and self._skill_registry is not None
                    and self._skill_actor is not None
                ):
                    payload = invoke_load_skill_docs(
                        self._skill_registry,
                        self._skill_actor,
                        tc.get("arguments") or "{}",
                        token_budget=self._skill_doc_token_budget,
                    )
                elif is_local_memory_tool(name) and self._memory_tools is not None:
                    payload = invoke_memory_tool(
                        self._memory_tools,
                        name,
                        tc.get("arguments") or "{}",
                    )
                else:
                    payload = self._invoke_one(
                        tc,
                        actor_member_id=actor_member_id,
                        team_id=team_id,
                        role=role,
                    )
                tool_payloads.append(payload)
                emit_trace(
                    {
                        "type": "tool",
                        "round": round_no,
                        "tool_call_id": tc.get("id") or "",
                        "name": name,
                        "arguments": tc.get("arguments") or "{}",
                        "result": payload,
                    }
                )
                logger.info(
                    "reply.timing stage=tool_invoke_done subject_id=%s tool=%s elapsed_ms=%d",
                    self._subject_id,
                    name,
                    int((time.monotonic() - run_t0) * 1000),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "",
                        "content": payload,
                    }
                )
                self._inject_inbox_messages(messages, inbox)

            verbatim = self._try_verbatim_private_reply(
                tool_calls,
                tool_payloads,
                conversation_type=conversation_type,
            )
            if verbatim is not None:
                return verbatim

        return _MAX_ROUNDS_MSG

    def _try_verbatim_private_reply(
        self,
        tool_calls: list[dict],
        tool_payloads: list[str],
        *,
        conversation_type: str,
    ) -> str | None:
        if conversation_type != "private" or len(tool_calls) != 1:
            return None
        tc = tool_calls[0]
        if is_builtin_tool(tc.get("name") or ""):
            return None
        cap = resolve_capability_for_tool_name(
            tc.get("name") or "",
            self._capabilities,
        )
        if cap is None or cap.key not in VERBATIM_PRIVATE_CAPABILITIES:
            return None
        try:
            data = json.loads(tool_payloads[0])
        except json.JSONDecodeError:
            return None
        if not data.get("ok"):
            return None
        message = str(data.get("user_message") or "").strip()
        return message or None

    def _invoke_builtin(
        self,
        tc: dict,
        *,
        emit_interim: Callable[[str], bool] | Callable[[str], None],
    ) -> str:
        name = tc.get("name") or ""
        if name == NOTIFY_USER_TOOL_NAME:
            try:
                args = json.loads(tc.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                return json.dumps(
                    {"ok": False, "error": "invalid JSON arguments"},
                    ensure_ascii=False,
                )
            message = str(args.get("message") or "").strip()
            if message:
                emit_interim(message)
            return json.dumps(
                {"ok": True, "delivered": bool(message)},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": False, "error": f"unknown builtin tool: {name}"},
            ensure_ascii=False,
        )

    def _inject_inbox_messages(
        self,
        messages: list[dict[str, Any]],
        inbox: TurnInbox | None,
    ) -> None:
        if inbox is None:
            return
        for entry in inbox.drain_unconsumed():
            text = (entry.text or "").strip()
            if not text:
                inbox.mark_consumed(entry.message_id)
                continue
            messages.append({"role": "user", "content": text})
            inbox.mark_consumed(entry.message_id)

    def _invoke_one(
        self,
        tc: dict,
        *,
        actor_member_id: str,
        team_id: str,
        role: str | None,
    ) -> str:
        name = tc.get("name") or ""
        cap = resolve_capability_for_tool_name(name, self._capabilities)
        if cap is None:
            return json.dumps(
                {"ok": False, "error": f"unknown or unauthorized tool: {name}"},
                ensure_ascii=False,
            )
        try:
            args = json.loads(tc.get("arguments") or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            return json.dumps(
                {"ok": False, "error": "invalid JSON arguments"},
                ensure_ascii=False,
            )
        try:
            result = self._executor.invoke(
                actor_member_id=actor_member_id,
                team_id=team_id,
                role=role,
                capability_key=cap.key,
                arguments=args,
                confirmed=True,
                capability_version=cap.version,
            )
            return json.dumps(
                {
                    "ok": result.status == "succeeded",
                    "status": result.status,
                    "user_message": result.user_message,
                    "result": result.result,
                },
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            logger.exception(
                "tool invoke failed name=%s subject=%s", name, self._subject_id
            )
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
