from __future__ import annotations

from assistant_platform.skills.models import SkillCard, SkillDocResult
from assistant_platform.skills.registry import DEFAULT_SKILL_WINDOW_LINES


def format_skill_cards_block(
    cards: list[SkillCard],
    *,
    previews: dict[str, SkillDocResult] | None = None,
) -> str:
    if not cards:
        return (
            "## 可用技能\n\n"
            "（本轮未匹配到专项技能；可正常陪聊。"
            "若用户提出明确业务请求，再根据需要调用 load_skill_docs。）"
        )
    preview_map = previews or {}
    lines = ["## 可用技能", ""]
    for card in cards:
        lines.append(f"### {card.name} (`{card.skill_id}`)")
        lines.append(card.summary)
        if card.when_to_use:
            lines.append("适用场景:")
            for item in card.when_to_use:
                lines.append(f"- {item}")
        if card.privacy == "private":
            lines.append("- 隐私：Key 相关操作建议私聊")
        preview = preview_map.get(card.skill_id)
        if preview is not None:
            lines.append(
                f"<!-- skill_preview skill_id={card.skill_id} "
                f"total_lines={preview.total_lines} "
                f"loaded_lines={preview.loaded_lines} "
                f"start_line={preview.start_line} "
                f"end_line={preview.end_line} -->"
            )
            lines.append(
                f"正文预览（已载入 {preview.loaded_lines}/{preview.total_lines} 行，"
                f"行 {preview.start_line}–{preview.end_line}）："
            )
            if preview.markdown.strip():
                lines.append(preview.markdown.rstrip())
            if preview.has_more and preview.next_start_line is not None:
                lines.append(
                    f"尚有后续正文未载入。需要时调用 "
                    f"load_skill_docs(skill_id=\"{card.skill_id}\", "
                    f"start_line={preview.next_start_line}) 续读"
                    f"（默认每次最多 {DEFAULT_SKILL_WINDOW_LINES} 行）。"
                )
            else:
                lines.append("（本 skill 正文已完整载入，无需再调用 load_skill_docs。）")
        lines.append("")
    return "\n".join(lines).strip()
