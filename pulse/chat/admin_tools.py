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
                name="nudge_unsubmitted",
                capability="tasks:nudge",
                description="私聊催促尚未提交本月用量的成员",
                handler=_nudge_unsubmitted,
            )
        )
        self.register(
            AdminTool(
                name="run_aggregate",
                capability="metrics:aggregate",
                description="重新聚合指定账期的团队用量指标",
                handler=_run_aggregate,
            )
        )
        self.register(
            AdminTool(
                name="publish_report",
                capability="reports:publish",
                description="生成并发布月报到钉钉群",
                handler=_publish_report,
            )
        )
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
                description="在钉钉群发送提醒消息（非月报）",
                handler=_send_group_tip,
            )
        )


_PARAM_SCHEMAS: dict[str, dict] = {
    "nudge_unsubmitted": {
        "type": "object",
        "properties": {
            "period": {"type": "string", "description": "账期 YYYY-MM，默认当前账期"},
            "tip": {"type": "string", "description": "催办附言"},
        },
    },
    "run_aggregate": {
        "type": "object",
        "properties": {
            "period": {"type": "string", "description": "账期 YYYY-MM"},
        },
        "required": ["period"],
    },
    "publish_report": {
        "type": "object",
        "properties": {"period": {"type": "string"}},
        "required": ["period"],
    },
    "run_evolution": {"type": "object", "properties": {}},
    "send_group_tip": {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
}


def _nudge_unsubmitted(ctx: AdminContext, args: dict) -> ToolResult:
    from pulse.periods import current_period

    if not ctx.messenger:
        return ToolResult(
            tool="nudge_unsubmitted",
            status="skipped",
            message="当前通道未连接钉钉，无法私聊催办。",
            capability="tasks:nudge",
        )
    period = args.get("period") or current_period(ctx.config)
    tip = args.get("tip") or "本月 Cursor 用量还没收到，方便的话私聊发我 CSV 哈～"
    members = ctx.repo.get_unsubmitted_members(period)
    sent = 0
    for member in members:
        ctx.messenger.send_oto_text(
            member.dingtalk_user_id,
            f"Hi {member.display_name}，{tip}",
        )
        sent += 1
    return ToolResult(
        tool="nudge_unsubmitted",
        status="executed",
        message=f"已私聊催促 {sent} 位未提交成员（{period}）。",
        capability="tasks:nudge",
        detail={"period": period, "count": sent},
    )


def _run_aggregate(ctx: AdminContext, args: dict) -> ToolResult:
    from pulse.aggregate.engine import aggregate_period
    from pulse.periods import current_period

    period = args.get("period") or current_period(ctx.config)
    metrics = aggregate_period(ctx.session, period, team_id=ctx.team_id)
    return ToolResult(
        tool="run_aggregate",
        status="executed",
        message=(
            f"✅ {period} 聚合完成：{metrics['total_events']} 条事件，"
            f"Tokens {metrics['total_tokens']:,}，付费 ${metrics['total_cost_usd']:.2f}"
        ),
        capability="metrics:aggregate",
        detail={"period": period},
    )


def _publish_report(ctx: AdminContext, args: dict) -> ToolResult:
    from pulse.periods import current_period
    from pulse.report.service import publish_report_to_group

    if not ctx.messenger:
        return ToolResult(
            tool="publish_report",
            status="skipped",
            message="需要钉钉机器人连接才能发群月报。",
            capability="reports:publish",
        )
    period = args.get("period") or current_period(ctx.config)
    try:
        body = publish_report_to_group(
            ctx.session,
            period,
            ctx.messenger,
            team_id=ctx.team_id,
            config=ctx.config,
        )
    except ValueError as exc:
        return ToolResult(
            tool="publish_report",
            status="failed",
            message=str(exc),
            capability="reports:publish",
        )
    preview = body[:300] + ("..." if len(body) > 300 else "")
    return ToolResult(
        tool="publish_report",
        status="executed",
        message=f"✅ {period} 月报已发布到群。\n{preview}",
        capability="reports:publish",
        detail={"period": period},
    )


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
    message = args.get("message") or "小提示：个人 Cursor 用量建议私聊机器人提交。"
    ctx.messenger.send_group_text(message, at_all=False)
    return ToolResult(
        tool="send_group_tip",
        status="executed",
        message="已在群里发送提醒。",
        capability="tasks:group_message",
    )


DEFAULT_ROUTER = AdminToolRouter()
