from __future__ import annotations

import logging

from personamem.domain import EvolutionActionProposal, EvolutionActionResult
from personamem.evolution import ActionExecutor
from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.storage.repository import Repository

logger = logging.getLogger(__name__)


class PulseActionExecutor(ActionExecutor):
    def __init__(
        self,
        *,
        config: AppConfig,
        pulse_repo: Repository,
        team_id: str,
        send_private_message=None,
        send_group_message=None,
    ):
        self.config = config
        self.pulse_repo = pulse_repo
        self.team_id = team_id
        self.send_private_message = send_private_message
        self.send_group_message = send_group_message

    def execute(
        self,
        *,
        namespace: str,
        action: EvolutionActionProposal,
    ) -> EvolutionActionResult:
        try:
            if action.action_type == "private_nudge_unsubmitted":
                return self._nudge_unsubmitted(action)
            if action.action_type == "admin_notify":
                return self._admin_notify(action)
            if action.action_type == "group_collection_tip":
                return self._group_tip(action)
            return EvolutionActionResult(
                action_type=action.action_type,
                status="skipped",
                detail="unknown action",
            )
        except Exception as exc:
            logger.exception("Evolution action failed: %s", action.action_type)
            return EvolutionActionResult(
                action_type=action.action_type,
                status="failed",
                detail=str(exc),
            )

    def _nudge_unsubmitted(self, action: EvolutionActionProposal) -> EvolutionActionResult:
        if not self.send_private_message:
            return EvolutionActionResult(
                action_type=action.action_type,
                status="skipped",
                detail="no messenger",
            )
        period = current_period(self.config)
        members = self.pulse_repo.get_unsubmitted_members(period)
        tip = action.payload.get("tip", "本月 Cursor 用量还没收到，方便的话私聊发我 CSV 哈～")
        sent = 0
        for member in members:
            self.send_private_message(member.dingtalk_user_id, f"Hi {member.display_name}，{tip}")
            sent += 1
        return EvolutionActionResult(
            action_type=action.action_type,
            status="executed",
            detail=f"nudged {sent} members",
        )

    def _admin_notify(self, action: EvolutionActionProposal) -> EvolutionActionResult:
        if not self.send_private_message:
            return EvolutionActionResult(
                action_type=action.action_type,
                status="skipped",
                detail="no messenger",
            )
        msg = action.payload.get("message", "记忆系统观察到需要关注的模式。")
        admin_ids = self.config.admin.dingtalk_user_ids
        if not admin_ids:
            return EvolutionActionResult(
                action_type=action.action_type,
                status="skipped",
                detail="no admins configured",
            )
        for admin_id in admin_ids:
            self.send_private_message(admin_id, f"📌 数字员工观察\n\n{msg}")
        return EvolutionActionResult(
            action_type=action.action_type,
            status="executed",
            detail=f"notified {len(admin_ids)} admins",
        )

    def _group_tip(self, action: EvolutionActionProposal) -> EvolutionActionResult:
        if not self.send_group_message:
            return EvolutionActionResult(
                action_type=action.action_type,
                status="skipped",
                detail="no group messenger",
            )
        msg = action.payload.get(
            "message",
            "小提示：个人 Cursor 用量建议私聊机器人提交，群里不会公开你的数字。",
        )
        self.send_group_message(msg, at_all=False)
        return EvolutionActionResult(
            action_type=action.action_type,
            status="executed",
            detail="group tip sent",
        )
