from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personamem.domain import SourceVisibility, VisibilityContext

from pulse.chat.admin_tools import DEFAULT_ROUTER, AdminContext, AdminToolRouter, ToolResult
from pulse.chat.planner import plan_admin_tools
from pulse.memory_adapter.identity import team_id_to_namespace
from pulse.memory_adapter.wiring import build_memory_engine
from pulse.web.audit import log_admin_action
from pulse.web.permissions import can_access_portal


@dataclass
class ChatAction:
    tool: str
    status: str
    message: str
    capability: str | None = None


@dataclass
class ChatResult:
    reply: str
    actions: list[ChatAction] = field(default_factory=list)


class ChatService:
    """钉钉与 Web 共用的小脉对话服务（记忆 + 管理工具）。"""

    def __init__(
        self,
        config,
        *,
        session_factory=None,
        messenger=None,
        router: AdminToolRouter | None = None,
    ):
        self.config = config
        self.session_factory = session_factory
        self.messenger = messenger
        self.router = router or DEFAULT_ROUTER

    def chat(
        self,
        *,
        session,
        team,
        repo,
        member,
        message: str,
        channel: str,
        is_group: bool = False,
        display_name: str | None = None,
    ) -> ChatResult:
        display_name = display_name or member.display_name
        namespace = team_id_to_namespace(team.id)
        subject_id = member.id

        if is_group:
            context = VisibilityContext.public()
            visibility = SourceVisibility.PUBLIC
        else:
            context = VisibilityContext.private(audience_id=subject_id)
            visibility = SourceVisibility.PRIVATE

        engine = build_memory_engine(
            session,
            self.config,
            team.id,
            pulse_repo=repo,
            send_private_message=self._send_private,
            send_group_message=self._send_group,
        )

        engine.record_turn(
            namespace=namespace,
            subject_id=subject_id,
            role="user",
            content=message,
            visibility=visibility,
        )

        tool_results = self._run_admin_tools(
            session=session,
            team_id=team.id,
            repo=repo,
            member=member,
            message=message,
            channel=channel,
        )

        disclosure = engine.recall(
            namespace=namespace,
            subject_ids=[subject_id],
            context=context,
            query=message,
        )

        augmented_message = message
        if tool_results:
            notes = "\n".join(f"[{r.tool}] {r.message}" for r in tool_results if r.message)
            augmented_message = f"{message}\n\n（系统任务结果）\n{notes}"

        reply = engine.reply(
            namespace=namespace,
            subject_ids=[subject_id],
            context=context,
            user_message=augmented_message,
            display_name=display_name,
            is_group=is_group,
            disclosure=disclosure,
            subject_id=subject_id,
        )

        if tool_results and not self.config.llm.enabled:
            extra = "\n\n".join(r.message for r in tool_results if r.status == "executed")
            if extra:
                reply = f"{reply}\n\n{extra}"

        engine.record_turn(
            namespace=namespace,
            subject_id=subject_id,
            role="assistant",
            content=reply,
            visibility=visibility,
        )

        engine.distill(
            namespace=namespace,
            subject_id=subject_id,
            context=context,
            transcript=f"用户: {message}\n助手: {reply}",
        )

        actions = [
            ChatAction(tool=r.tool, status=r.status, message=r.message, capability=r.capability)
            for r in tool_results
        ]
        return ChatResult(reply=reply, actions=actions)

    def _run_admin_tools(
        self,
        *,
        session,
        team_id: str,
        repo,
        member,
        message: str,
        channel: str,
    ) -> list[ToolResult]:
        if not can_access_portal(member):
            return []

        plans = plan_admin_tools(message, member, self.router, config=self.config)
        if not plans:
            return []

        ctx = AdminContext(
            config=self.config,
            session=session,
            team_id=team_id,
            repo=repo,
            member=member,
            channel=channel,
            messenger=self.messenger,
            session_factory=self.session_factory,
        )

        results: list[ToolResult] = []
        for tool_name, args in plans:
            result = self.router.execute(ctx, tool_name, args)
            results.append(result)
            if result.status in ("executed", "denied", "failed"):
                log_admin_action(
                    session,
                    team_id=team_id,
                    member_id=member.id,
                    action=f"chat.tool.{tool_name}",
                    capability=result.capability,
                    detail=result.message[:500],
                    channel=channel,
                )
        return results

    def _send_private(self, user_id: str, text: str) -> None:
        if self.messenger:
            self.messenger.send_oto_text(user_id, text)

    def _send_group(self, text: str, at_all: bool = False) -> None:
        if self.messenger:
            self.messenger.send_group_text(text, at_all=at_all)
