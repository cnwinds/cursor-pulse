from __future__ import annotations

from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult

from pulse.capabilities.handlers.common import resolve_actor_member
from pulse.periods import current_period
from pulse.tool_center.knowledge import KnowledgeService, TipSubmissionError


def _svc(session, team_id: str, config: Any) -> KnowledgeService:
    return KnowledgeService(session, team_id, config)


def handle_knowledge_tip_create(
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
    title = str(args.get("title") or "").strip()
    body = str(args.get("body") or "").strip()
    if not title or not body:
        return CapabilityInvokeResult(
            status="failed",
            error_code="invalid_arguments",
            user_message=(
                "缺少 title 或 body。请先与用户确认技巧标题与 Markdown 正文"
                "（含技巧说明、操作步骤），再提交。"
            ),
        )

    tags = args.get("tags")
    tag_list = list(tags) if isinstance(tags, list) else None
    vendor_slug = args.get("vendor_slug")
    vendor_slug_str = str(vendor_slug).strip() if vendor_slug else None
    period = args.get("period")
    period_str = str(period).strip() if period else current_period(config)

    svc = _svc(session, request.team_id, config)
    try:
        entry = svc.create_from_submission(
            author=member,
            title=title,
            body=body,
            tags=tag_list,
            vendor_slug=vendor_slug_str,
            source_channel="dingtalk_dm",
            period=period_str,
            raw_input=str(args.get("raw_input") or body),
        )
        session.flush()
    except TipSubmissionError as exc:
        session.rollback()
        return CapabilityInvokeResult(
            status="failed",
            error_code="tip_quality_rejected",
            user_message=str(exc),
            result={"approved": False},
        )
    except Exception as exc:
        session.rollback()
        return CapabilityInvokeResult(
            status="failed",
            error_code="command_failed",
            user_message=f"心得保存失败：{exc}",
        )

    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "entry_id": entry.id,
            "title": entry.title,
            "tags": list(entry.tags or []),
        },
    )


def handle_knowledge_tip_list(
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
    period = args.get("period")
    period_str = str(period).strip() if period else None
    limit = args.get("limit")
    try:
        limit_n = int(limit) if limit is not None else 20
    except (TypeError, ValueError):
        limit_n = 20
    limit_n = max(1, min(limit_n, 50))

    svc = _svc(session, request.team_id, config)
    entries = svc.list_entries(period=period_str)[:limit_n]
    titles = [
        {
            "id": e.id,
            "title": e.title,
            "period": e.period,
            "author": e.author_member.display_name if e.author_member else None,
        }
        for e in entries
    ]
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "entries": titles,
            "count": len(titles),
            "period": period_str,
        },
    )


def handle_knowledge_tip_read(
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
    entry_id = str(args.get("entry_id") or "").strip()
    title_query = str(args.get("title_query") or "").strip()

    svc = _svc(session, request.team_id, config)
    entry = None
    if entry_id:
        entry = svc.get_entry(entry_id)
    elif title_query:
        matches = svc.find_entries_by_title(title_query, limit=5)
        if len(matches) == 1:
            entry = matches[0]
        elif len(matches) > 1:
            return CapabilityInvokeResult(
                status="succeeded",
                user_message="",
                result={
                    "schema_version": 1,
                    "empty_reason": "ambiguous_title",
                    "matches": [{"id": m.id, "title": m.title} for m in matches],
                },
            )
    else:
        return CapabilityInvokeResult(
            status="failed",
            error_code="invalid_arguments",
            user_message="请提供 entry_id 或 title_query 以查看技巧详情",
        )

    if entry is None:
        return CapabilityInvokeResult(
            status="failed",
            error_code="not_found",
            user_message="未找到对应技巧，可先调用技巧库列表查看标题",
        )

    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={
            "schema_version": 1,
            "entry_id": entry.id,
            "title": entry.title,
            "body": entry.body,
            "tags": entry.tags or [],
            "author": entry.author_member.display_name if entry.author_member else None,
            "period": entry.period,
        },
    )
