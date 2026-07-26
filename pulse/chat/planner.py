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
        "若只是闲聊、问记忆、绑定 Key 等，不要调用工具。"
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
    perms = resolve_permissions(member)
    plans: list[tuple[str, dict[str, Any]]] = []

    def can(cap: str) -> bool:
        return cap in perms

    if can("evolution:run") and re.search(r"进化|自我总结|evolution", text, re.I):
        plans.append(("run_evolution", {}))

    if can("tasks:group_message") and re.search(r"群里.*提醒|群消息|group.*tip", text, re.I):
        msg = re.sub(r"^.*?(提醒|说)[：:]?", "", text).strip() or None
        plans.append(("send_group_tip", {"message": msg or text}))

    return plans
