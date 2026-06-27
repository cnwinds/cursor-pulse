from __future__ import annotations

import json
import re
from typing import Any

from pulse.chat.admin_tools import AdminToolRouter
from pulse.llm.client import build_llm_client
from pulse.web.permissions import resolve_permissions


def plan_admin_tools(
    message: str,
    member,
    router: AdminToolRouter,
    *,
    config,
) -> list[tuple[str, dict[str, Any]]]:
    """返回 [(tool_name, args), ...]，先 LLM 再规则兜底。"""
    available = router.list_for_member(member)
    if not available:
        return []

    llm_plans = _plan_with_llm(message, member, router, config)
    if llm_plans:
        return llm_plans
    return _plan_with_rules(message, member, router)


def _plan_with_llm(
    message: str,
    member,
    router: AdminToolRouter,
    config,
) -> list[tuple[str, dict[str, Any]]]:
    if not config.llm.enabled:
        return []
    client = build_llm_client(config)
    if client is None or not hasattr(client, "complete_with_tools"):
        return []

    tools = router.tool_schemas(member)
    if not tools:
        return []

    system = (
        "你是小脉的任务规划器。根据用户自然语言，决定是否调用管理工具。"
        "若只是闲聊、问记忆、提交 CSV 等，不要调用工具。"
        "需要执行任务时才调用对应 function。"
    )
    try:
        result = client.complete_with_tools(system=system, user=message, tools=tools)
    except Exception:
        return []

    plans: list[tuple[str, dict[str, Any]]] = []
    for call in result.get("tool_calls", []):
        name = call.get("name")
        if not name:
            continue
        args = call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        plans.append((name, args))
    return plans


def _plan_with_rules(
    message: str,
    member,
    router: AdminToolRouter,
) -> list[tuple[str, dict[str, Any]]]:
    text = message.strip()
    lower = text.lower()
    perms = resolve_permissions(member)
    plans: list[tuple[str, dict[str, Any]]] = []

    def can(cap: str) -> bool:
        return cap in perms

    period_match = re.search(r"(20\d{2}-\d{2})", text)
    period = period_match.group(1) if period_match else None

    if can("tasks:nudge") and re.search(r"催|未交|没交|nudge", text, re.I):
        plans.append(("nudge_unsubmitted", {"period": period} if period else {}))

    if can("metrics:aggregate") and re.search(r"聚合|重跑|aggregate", text, re.I):
        plans.append(("run_aggregate", {"period": period or ""}))

    if can("reports:publish") and re.search(r"月报|发群|发布.*报告|publish", text, re.I):
        plans.append(("publish_report", {"period": period or ""}))

    if can("evolution:run") and re.search(r"进化|自我总结|evolution", text, re.I):
        plans.append(("run_evolution", {}))

    if can("tasks:group_message") and re.search(r"群里.*提醒|群消息|group.*tip", text, re.I):
        msg = re.sub(r"^.*?(提醒|说)[：:]?", "", text).strip() or None
        plans.append(("send_group_tip", {"message": msg or text}))

    if can("submissions:read") and re.search(r"待审|pending|审核列表", text, re.I):
        plans.append(("list_pending_reviews", {"period": period} if period else {}))

    if can("submissions:review"):
        m = re.search(r"确认\s*([0-9a-fA-F]{6,8})", text)
        if m:
            plans.append(("confirm_submission", {"prefix": m.group(1)}))

    return plans
