from __future__ import annotations

from personamem.domain import DeflectionReason, DisclosureResult
from personamem.persona import Persona
from personamem.reply import build_reply_prompt, deflection_hint, format_conversation_context
from personamem.ports import Responder


class RuleBasedResponder(Responder):
    """无 LLM 时的模板回复。"""

    def reply(
        self,
        *,
        persona: Persona,
        user_message: str,
        display_name: str,
        disclosure: DisclosureResult,
        ranked_atoms: list,
        is_group: bool,
        recent_turns=None,
        schedule_note: str | None = None,
        now=None,
    ) -> str:
        prefix = ""
        if schedule_note:
            prefix = "（这会儿我下班了，明天上班再细聊也行）"

        if disclosure.deflection_reason == DeflectionReason.COMMITMENT and is_group:
            return prefix + (
                f"{display_name}，这个我更适合私聊细说哈～"
                "要是方便的话，把 Cursor Usage 的 CSV 私聊发我也行。"
            )
        if disclosure.deflection_reason == DeflectionReason.PRIVACY_DEFAULT and is_group:
            return prefix + (
                f"{display_name}，个人明细我一般不在群里点名哈，"
                "要看自己的私聊我就好。有 CSV 也可以直接发我。"
            )
        if disclosure.deflection_reason == DeflectionReason.BOTTOM_LINE:
            return prefix + f"{display_name}，这个涉及隐私，我不能说。"

        if ranked_atoms:
            memory_hint = ranked_atoms[0].content
            return prefix + (
                f"Hi {display_name}，我是{persona.name}。"
                f"记得你之前提过：{memory_hint}。"
                "需要提交本月 Cursor 用量的话，把 Dashboard 导出的 CSV 发我就行。"
            )

        return prefix + (
            f"Hi {display_name}，我是{persona.name}，{persona.role}。"
            "请发送 Cursor Usage 导出的 CSV 文件，或直接粘贴 CSV 内容。"
        )


class LlmResponder(Responder):
    def __init__(self, client):
        self._client = client

    def reply(
        self,
        *,
        persona: Persona,
        user_message: str,
        display_name: str,
        disclosure: DisclosureResult,
        ranked_atoms: list,
        is_group: bool,
        recent_turns=None,
        schedule_note: str | None = None,
        now=None,
    ) -> str:
        system, user = build_reply_prompt(
            persona=persona,
            user_message=user_message,
            display_name=display_name,
            disclosure=disclosure,
            ranked_atoms=ranked_atoms,
            is_group=is_group,
            recent_turns=recent_turns or [],
            schedule_note=schedule_note,
            now=now,
        )
        hint = deflection_hint(disclosure.deflection_reason)
        if hint:
            system += f"\n{hint}"
        return self._client.complete(system=system, user=user)
