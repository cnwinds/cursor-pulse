from __future__ import annotations

import json
from typing import Any

from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import DEFAULT_SKILL_WINDOW_LINES, SkillRegistry

LOAD_SKILL_DOCS_TOOL_NAME = "load_skill_docs"


def load_skill_docs_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": LOAD_SKILL_DOCS_TOOL_NAME,
            "description": (
                "按需加载或续读某项技能的 Markdown 说明书。"
                "命中技能时 system 已注入正文前若干行；"
                "若 loaded_lines < total_lines，用 start_line 续读后续行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                    "start_line": {
                        "type": "integer",
                        "description": (
                            "从正文第几行开始读取（1-based）。"
                            "续读时传上一段返回的 next_start_line。"
                        ),
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": (
                            f"本次最多返回多少行，默认 {DEFAULT_SKILL_WINDOW_LINES}。"
                        ),
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "已废弃，忽略：现每个 skill_id 对应单个 Markdown 文件。"
                        ),
                    },
                },
                "required": ["skill_id"],
                "additionalProperties": False,
            },
        },
    }


def is_local_skill_tool(tool_name: str) -> bool:
    return tool_name == LOAD_SKILL_DOCS_TOOL_NAME


def invoke_load_skill_docs(
    registry: SkillRegistry,
    actor: SkillActorContext,
    arguments: str,
    *,
    token_budget: int,
) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    skill_id = str(args.get("skill_id") or "").strip()
    if not skill_id:
        return json.dumps({"ok": False, "error": "缺少 skill_id"}, ensure_ascii=False)
    start_line = args.get("start_line", 1)
    max_lines = args.get("max_lines", DEFAULT_SKILL_WINDOW_LINES)
    try:
        start_line_i = int(start_line)
    except (TypeError, ValueError):
        start_line_i = 1
    try:
        max_lines_i = int(max_lines)
    except (TypeError, ValueError):
        max_lines_i = DEFAULT_SKILL_WINDOW_LINES
    try:
        result = registry.load_docs(
            skill_id,
            actor=actor,
            token_budget=token_budget,
            start_line=start_line_i,
            max_lines=max_lines_i,
        )
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(
        {
            "ok": True,
            "skill_id": result.skill_id,
            "total_lines": result.total_lines,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "loaded_lines": result.loaded_lines,
            "has_more": result.has_more,
            "next_start_line": result.next_start_line,
            "truncated": result.truncated,
            "markdown": result.markdown,
        },
        ensure_ascii=False,
    )
