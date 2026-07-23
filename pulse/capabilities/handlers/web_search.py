from __future__ import annotations

import logging
from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult

from pulse.capabilities.handlers.common import resolve_actor_member
from assistant_platform.memory.observability import log_web_search
from pulse.capabilities.web.provider import SearchProviderError, get_search_provider
from pulse.capabilities.web.rate_limit import check_web_rate_limit

logger = logging.getLogger(__name__)


def _format_search_message(payload: dict[str, Any]) -> str:
    results = payload.get("results") or []
    if not results:
        return "未找到相关联网结果。"
    lines = [
        f"联网搜索完成（提供商={payload.get('provider')}，条数={payload.get('result_count')}，"
        f"检索时间={payload.get('retrieved_at')}）："
    ]
    for item in results:
        rank = item.get("rank")
        title = item.get("title") or item.get("url")
        url = item.get("url")
        snippet = (item.get("snippet") or "").strip()
        if len(snippet) > 180:
            snippet = snippet[:177] + "…"
        published = item.get("published_at")
        extra = f"；发布={published}" if published else ""
        lines.append(f"{rank}. {title}\n   {url}{extra}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


def handle_web_search(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = resolve_actor_member(session, request)
    if member is None:
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="成员不存在或无权访问",
        )

    args = request.arguments or {}
    query = str(args.get("query") or args.get("text") or "").strip()
    if not query:
        return CapabilityInvokeResult(
            status="failed",
            error_code="invalid_arguments",
            user_message="请提供搜索词 query（仅来自当前用户请求，勿注入私人历史）",
        )

    max_results = args.get("max_results")
    try:
        max_n = int(max_results) if max_results is not None else None
    except (TypeError, ValueError):
        max_n = None

    web_cfg = getattr(config, "web_search", None)
    limit = int(getattr(web_cfg, "rate_limit_per_minute", 30) or 0)
    allowed, retry_after = check_web_rate_limit(request.team_id, limit)
    if not allowed:
        log_web_search(status="failed", error_code="rate_limit_exceeded", retryable=True)
        msg = "联网搜索请求过于频繁，请稍后再试"
        if retry_after:
            msg = f"{msg}（约 {retry_after} 秒后重试）"
        return CapabilityInvokeResult(
            status="failed",
            error_code="rate_limit_exceeded",
            user_message=msg,
            retryable=True,
            result={
                "provider_status": "failed",
                "error_code": "rate_limit_exceeded",
                "retry_after_seconds": retry_after,
            },
        )

    try:
        provider = get_search_provider(config)
        response = provider.search(query, max_results=max_n)
    except SearchProviderError as exc:
        log_web_search(
            status="failed",
            error_code=exc.error_code,
            retryable=exc.retryable,
        )
        return CapabilityInvokeResult(
            status="failed",
            error_code=exc.error_code,
            user_message=str(exc),
            retryable=exc.retryable,
            result={"provider_status": "failed", "error_code": exc.error_code},
        )
    except Exception:
        log_web_search(status="failed", error_code="provider_error", retryable=True)
        logger.exception("web.search unexpected failure")
        return CapabilityInvokeResult(
            status="failed",
            error_code="provider_error",
            user_message="联网搜索失败",
            retryable=True,
            result={"provider_status": "failed"},
        )

    payload = response.to_dict()
    log_web_search(
        status="succeeded",
        provider=str(payload.get("provider") or ""),
        result_count=int(payload.get("result_count") or 0),
    )
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            **payload,
            "provider_status": "succeeded",
        },
    )
