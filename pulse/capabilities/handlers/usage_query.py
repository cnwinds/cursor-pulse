from __future__ import annotations

import re

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.authz.actor import is_channel_admin
from pulse.capabilities.handlers.common import resolve_actor_member
from pulse.query.llm_query import (
    LLM_UNAVAILABLE_MESSAGE,
    answer_usage_with_llm,
    build_llm_client_from_app_config,
)

_QUERY_PREFIX_RE = re.compile(r"^(?:查询|问)\s*", re.IGNORECASE)


def strip_usage_query_text(text: str) -> str:
    """Remove optional「查询/问」prefix; intent parsing is done by LLM upstream."""
    stripped = (text or "").strip()
    if not stripped:
        return stripped
    return _QUERY_PREFIX_RE.sub("", stripped, count=1).strip() or stripped


def handle_usage_query(
    session,
    *,
    request: CapabilityInvokeRequest,
    config,
    op,
) -> CapabilityInvokeResult:
    text = str(request.arguments.get("text") or "").strip()
    question = strip_usage_query_text(text) or text
    if not question:
        return CapabilityInvokeResult(
            status="failed",
            error_code="invalid_arguments",
            user_message="请说明你想查询什么，例如：查询 我本月 tokens 用了多少",
        )

    member = resolve_actor_member(session, request)
    if member is None:
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="成员不存在或无权访问",
        )

    client = build_llm_client_from_app_config(config)
    if client is None:
        return CapabilityInvokeResult(
            status="failed",
            error_code="llm_unavailable",
            user_message=LLM_UNAVAILABLE_MESSAGE,
        )

    try:
        reply = answer_usage_with_llm(
            session,
            question=question,
            config=config,
            member_name=member.display_name,
            member_id=member.id,
            is_admin=is_channel_admin(member, config),
            client=client,
        )
    except Exception as exc:
        return CapabilityInvokeResult(
            status="failed",
            error_code="llm_query_failed",
            user_message=f"查询失败：{exc}",
            retryable=True,
        )

    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "mode": "llm",
            "question": question,
            "answer": reply,
        },
    )
