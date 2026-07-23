"""Local memory tools bound to current subject/team (no Pulse HTTP)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from assistant_platform.config import AssistantChatMemoryConfig
from assistant_platform.memory.archive_search import (
    SearchScope,
    _session_is_in_scope,
    expand_neighbors,
    hybrid_search,
    read_message_range,
)
from assistant_platform.memory.contracts import ChunkAnchor, RecallCursor
from assistant_platform.memory.observability import log_memory_tool
from assistant_platform.memory.session_summary import load_session_summary
from assistant_platform.memory.semantic.domain import VisibilityContext

logger = logging.getLogger(__name__)

MEMORY_TOOL_KEYS = frozenset(
    {
        "memory.search",
        "memory.expand",
        "memory.get_session_summary",
        "memory.read_range",
    }
)

LOCAL_MEMORY_TOOL_NAMES = frozenset(
    {
        "memory_search",
        "memory_expand",
        "memory_get_session_summary",
        "memory_read_range",
    }
)


def tool_name_for_memory_key(key: str) -> str:
    return key.replace(".", "_")


def is_local_memory_tool(tool_name: str) -> bool:
    return tool_name in LOCAL_MEMORY_TOOL_NAMES


def memory_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": (
                    "搜索已关闭会话的脱敏历史片段（仅限当前用户/群作用域）。"
                    "支持 query 关键词与 cursor 续页。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "cursor": {
                            "type": "object",
                            "description": "可选分页游标（RecallCursor）",
                            "properties": {
                                "query_fingerprint": {"type": "string"},
                                "sort_key": {"type": "string"},
                                "offset": {"type": "integer"},
                            },
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_expand",
                "description": "以 ChunkAnchor 为中心展开相邻历史片段，获取前后上下文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "chunk_index": {"type": "integer"},
                        "start_seq": {"type": "integer"},
                        "end_seq": {"type": "integer"},
                    },
                    "required": ["session_id", "chunk_index", "start_seq", "end_seq"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_get_session_summary",
                "description": "获取指定已关闭会话的结构化摘要（不含全文）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                    },
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_read_range",
                "description": "按消息序号范围读取已关闭会话脱敏原文（需二次作用域校验）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "start_seq": {"type": "integer"},
                        "end_seq": {"type": "integer"},
                    },
                    "required": ["session_id", "start_seq", "end_seq"],
                    "additionalProperties": False,
                },
            },
        },
    ]


class MemoryToolService:
    """Execute memory tools locally with scope and disclosure re-checks."""

    def __init__(
        self,
        session: Session,
        *,
        config: AssistantChatMemoryConfig,
        scope: SearchScope,
        visibility_context: VisibilityContext,
    ) -> None:
        self._session = session
        self._config = config
        self._scope = scope
        self._visibility = visibility_context

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "memory_search":
            return self.search(
                str(arguments.get("query") or ""),
                cursor=_parse_cursor(arguments.get("cursor")),
            )
        if tool_name == "memory_expand":
            return self.expand(
                session_id=str(arguments.get("session_id") or ""),
                chunk_index=int(arguments.get("chunk_index") or 0),
                start_seq=int(arguments.get("start_seq") or 0),
                end_seq=int(arguments.get("end_seq") or 0),
            )
        if tool_name == "memory_get_session_summary":
            return self.get_session_summary(str(arguments.get("session_id") or ""))
        if tool_name == "memory_read_range":
            return self.read_range(
                session_id=str(arguments.get("session_id") or ""),
                start_seq=int(arguments.get("start_seq") or 0),
                end_seq=int(arguments.get("end_seq") or 0),
            )
        return {"ok": False, "error": f"unknown memory tool: {tool_name}"}

    def search(self, query: str, *, cursor: RecallCursor | None = None) -> dict[str, Any]:
        try:
            hits, page = hybrid_search(
                self._session,
                query=query,
                scope=self._scope,
                config=self._config,
                cursor=cursor,
            )
            log_memory_tool(
                tool="memory_search",
                team_id=self._scope.team_id,
                subject_id=self._scope.subject_id,
                ok=True,
                hit_count=len(hits),
            )
            return {
                "ok": True,
                "result": {
                    "fragments": [hit.model_dump(mode="json") for hit in hits],
                    "page": page.model_dump(mode="json"),
                },
            }
        except Exception as exc:
            log_memory_tool(
                tool="memory_search",
                team_id=self._scope.team_id,
                subject_id=self._scope.subject_id,
                ok=False,
            )
            logger.exception("memory_search failed")
            return {"ok": False, "error": str(exc)}

    def expand(
        self,
        *,
        session_id: str,
        chunk_index: int,
        start_seq: int,
        end_seq: int,
    ) -> dict[str, Any]:
        if not _session_in_scope(self._session, session_id, self._scope):
            return {"ok": False, "error": "session not in scope or not available"}
        anchor = ChunkAnchor(
            session_id=session_id,
            chunk_index=chunk_index,
            start_seq=start_seq,
            end_seq=end_seq,
        )
        window = expand_neighbors(
            self._session,
            anchor=anchor,
            scope=self._scope,
            neighbor_count=self._config.recall.expand_neighbor_count,
        )
        log_memory_tool(
            tool="memory_expand",
            team_id=self._scope.team_id,
            subject_id=self._scope.subject_id,
            ok=True,
            session_id=session_id,
            prev_count=len(window.prev_hits),
            next_count=len(window.next_hits),
        )
        return {"ok": True, "result": window.model_dump(mode="json")}

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        if not _session_in_scope(self._session, session_id, self._scope):
            return {"ok": False, "error": "session not in scope or not available"}
        summary = load_session_summary(self._session, session_id)
        if summary is None:
            return {"ok": False, "error": "summary not found"}
        return {"ok": True, "result": summary.model_dump(mode="json")}

    def read_range(
        self,
        *,
        session_id: str,
        start_seq: int,
        end_seq: int,
    ) -> dict[str, Any]:
        hits = read_message_range(
            self._session,
            session_id=session_id,
            start_seq=start_seq,
            end_seq=end_seq,
            scope=self._scope,
        )
        if not hits:
            return {"ok": False, "error": "range not available or out of scope"}
        return {
            "ok": True,
            "result": {
                "fragments": [hit.model_dump(mode="json") for hit in hits],
            },
        }


def _parse_cursor(raw: Any) -> RecallCursor | None:
    if not raw or not isinstance(raw, dict):
        return None
    try:
        return RecallCursor.model_validate(raw)
    except Exception:
        return None


def _session_in_scope(session: Session, session_id: str, scope: SearchScope) -> bool:
    return _session_is_in_scope(session, session_id, scope)


def invoke_memory_tool(service: MemoryToolService, tool_name: str, arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": "invalid JSON arguments"}, ensure_ascii=False)
    result = service.invoke(tool_name, args)
    return json.dumps(result, ensure_ascii=False, default=str)
