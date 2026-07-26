from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pulse.web.permissions import has_permission


@dataclass
class ToolResult:
    tool: str
    status: str
    message: str
    capability: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdminContext:
    config: Any
    session: Any
    team_id: str
    repo: Any
    member: Any
    channel: str
    messenger: Any | None = None
    session_factory: Any | None = None


ToolHandler = Callable[[AdminContext, dict[str, Any]], ToolResult]


@dataclass
class AdminTool:
    name: str
    capability: str
    description: str
    handler: ToolHandler


def _denied(tool: str, capability: str) -> ToolResult:
    return ToolResult(
        tool=tool,
        status="denied",
        message=f"你没有「{capability}」权限，这个我帮不了你。",
        capability=capability,
    )


class AdminToolRouter:
    """管理类任务工具路由：权限检查 + 执行 + 可审计。"""

    def __init__(self) -> None:
        self._tools: dict[str, AdminTool] = {}
        self._register_defaults()

    def register(self, tool: AdminTool) -> None:
        self._tools[tool.name] = tool

    def list_for_member(self, member) -> list[AdminTool]:
        return [t for t in self._tools.values() if has_permission(member, t.capability)]

    def tool_schemas(self, member) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": _PARAM_SCHEMAS.get(t.name, {"type": "object", "properties": {}}),
                },
            }
            for t in self.list_for_member(member)
        ]

    def execute(self, ctx: AdminContext, tool_name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(tool=tool_name, status="skipped", message=f"未知工具：{tool_name}")
        if not has_permission(ctx.member, tool.capability):
            return _denied(tool_name, tool.capability)
        try:
            return tool.handler(ctx, args or {})
        except Exception as exc:
            return ToolResult(
                tool=tool_name,
                status="failed",
                message=f"执行失败：{exc}",
                capability=tool.capability,
            )

    def _register_defaults(self) -> None:
        self.register(
            AdminTool(
                name="run_evolution",
                capability="evolution:run",
                description="运行记忆自进化（归纳原则与建议动作）",
                handler=_run_evolution,
            )
        )
        self.register(
            AdminTool(
                name="send_group_tip",
                capability="tasks:group_message",
                description="在钉钉群发送提醒消息",
                handler=_send_group_tip,
            )
        )


_PARAM_SCHEMAS: dict[str, dict] = {
    "run_evolution": {"type": "object", "properties": {}},
    "send_group_tip": {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
}


def _run_evolution(ctx: AdminContext, args: dict) -> ToolResult:
    return ToolResult(
        tool="run_evolution",
        status="skipped",
        message="记忆自进化已暂停，等待 assistant 语义记忆模块迁移完成。",
        capability="evolution:run",
    )


def _send_group_tip(ctx: AdminContext, args: dict) -> ToolResult:
    if not ctx.messenger:
        return ToolResult(
            tool="send_group_tip",
            status="skipped",
            message="需要钉钉机器人才能在群里发消息。",
            capability="tasks:group_message",
        )
    message = args.get("message") or "小提示：Cursor 用量绑定 API Key 后自动同步。"
    ctx.messenger.send_group_text(message, at_all=False)
    return ToolResult(
        tool="send_group_tip",
        status="executed",
        message="已在群里发送提醒。",
        capability="tasks:group_message",
    )


DEFAULT_ROUTER = AdminToolRouter()
