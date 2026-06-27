from __future__ import annotations

from datetime import datetime

from personamem.domain import DeflectionReason, DisclosureResult
from personamem.persona import Persona


def format_memory_context(atoms: list, *, max_items: int = 8) -> str:
    if not atoms:
        return "（暂无相关记忆）"
    lines = []
    for atom in atoms[:max_items]:
        lines.append(f"- [{atom.kind.value}] {atom.content}")
    return "\n".join(lines)


def format_conversation_context(turns: list, *, max_turns: int = 6) -> str:
    if not turns:
        return "（无近期对话）"
    lines = []
    for turn in turns[-max_turns:]:
        role = "同事" if turn.role == "user" else "你"
        lines.append(f"{role}: {turn.content}")
    return "\n".join(lines)


def deflection_hint(reason: DeflectionReason) -> str:
    if reason == DeflectionReason.COMMITMENT:
        return "有部分信息因承诺不能直说，请丝滑转移话题，不暴露承诺存在、不说谎。"
    if reason == DeflectionReason.PRIVACY_DEFAULT:
        return "涉及隐私的内容已过滤，请用公开口径或建议私聊。"
    if reason == DeflectionReason.BOTTOM_LINE:
        return "触及底线原则，请明确而礼貌地亮出边界。"
    return ""


def build_reply_prompt(
    *,
    persona: Persona,
    user_message: str,
    display_name: str,
    disclosure: DisclosureResult,
    ranked_atoms: list,
    is_group: bool,
    recent_turns: list | None = None,
    schedule_note: str | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    scene = "群聊（公开场景）" if is_group else "私聊"
    system = (
        persona.system_preamble(now=now)
        + "\n\n你只能基于下方「可引用记忆」作答，不得编造未列出的个人数据。"
        + "若用户要提交用量，引导其发送 Cursor Usage 导出的 CSV。"
    )
    if schedule_note:
        system += f"\n{schedule_note}"

    hint = deflection_hint(disclosure.deflection_reason)
    user = (
        f"场景：{scene}\n"
        f"同事：{display_name}\n"
        f"近期对话：\n{format_conversation_context(recent_turns or [])}\n\n"
        f"最新消息：{user_message}\n\n"
        f"可引用记忆：\n{format_memory_context(ranked_atoms)}\n"
    )
    if hint:
        user += f"\n回避提示：{hint}\n"
    user += "\n请用一两段自然中文回复，不要列表腔。"
    return system, user
