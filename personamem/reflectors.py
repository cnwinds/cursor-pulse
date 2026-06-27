from __future__ import annotations

import json
import re

from personamem.domain import EvolutionActionProposal, Principle
from personamem.evolution import LearnedPrincipleProposal, ReflectionResult, Reflector


class RuleBasedReflector(Reflector):
    """从近期披露日志归纳偏好原则，并提议可执行动作。"""

    def reflect(
        self,
        *,
        namespace: str,
        recent_atom_summaries: list[str],
        disclosure_summaries: list[str],
        existing_principles: list[Principle],
    ) -> ReflectionResult:
        principles: list[LearnedPrincipleProposal] = []
        actions: list[EvolutionActionProposal] = []

        commitment_hits = sum(1 for s in disclosure_summaries if s.startswith("commitment:"))
        privacy_hits = sum(1 for s in disclosure_summaries if s.startswith("privacy_default:"))

        if commitment_hits >= 2:
            principles.append(
                LearnedPrincipleProposal(
                    rule="群里被问到个人明细时，优先建议私聊对接",
                    origin=f"近7日 {commitment_hits} 次承诺类回避",
                    confidence=0.75,
                )
            )
        if privacy_hits >= 3:
            principles.append(
                LearnedPrincipleProposal(
                    rule="公开场景只讨论团队整体口径，不点名个人",
                    origin=f"近7日 {privacy_hits} 次隐私默认拦截",
                    confidence=0.8,
                )
            )
            actions.append(
                EvolutionActionProposal(
                    action_type="group_collection_tip",
                    payload={"message": "提醒：个人用量明细建议私聊机器人提交，群里不会公开数字。"},
                    reason="多次群场景隐私拦截",
                    confidence=0.78,
                )
            )
        if any("Opus" in s for s in recent_atom_summaries):
            principles.append(
                LearnedPrincipleProposal(
                    rule="涉及 Opus 用量的个人细节，默认私聊沟通",
                    origin="近期记忆多次提及 Opus",
                    confidence=0.72,
                )
            )

        if privacy_hits >= 2 or commitment_hits >= 1:
            actions.append(
                EvolutionActionProposal(
                    action_type="private_nudge_unsubmitted",
                    payload={"tip": "本月用量还没收到你的 CSV，方便的话私聊发我哈～"},
                    reason="结合隐私/承诺模式，提前私聊催办",
                    confidence=0.76,
                )
            )

        if commitment_hits >= 2:
            actions.append(
                EvolutionActionProposal(
                    action_type="admin_notify",
                    payload={"message": "近期多次出现承诺类披露回避，请关注团队隐私沟通习惯。"},
                    reason="承诺类拦截偏多",
                    confidence=0.7,
                )
            )

        return ReflectionResult(principles=principles, actions=actions)


class LlmReflector(Reflector):
    def __init__(self, client):
        self._client = client

    def reflect(
        self,
        *,
        namespace: str,
        recent_atom_summaries: list[str],
        disclosure_summaries: list[str],
        existing_principles: list[Principle],
    ) -> ReflectionResult:
        if not recent_atom_summaries and not disclosure_summaries:
            return ReflectionResult(principles=[], actions=[])

        payload = {
            "recent_atoms": recent_atom_summaries[:30],
            "disclosure_logs": disclosure_summaries[:30],
            "existing": [p.rule for p in existing_principles],
        }
        system = (
            "你是数字员工的自我反思模块。根据近期记忆与披露日志，"
            "提出 0-3 条偏好原则(learned)和 0-2 条可执行动作。"
            "动作类型仅限：private_nudge_unsubmitted, admin_notify, group_collection_tip。"
            '输出 JSON：{"principles":[{"rule":"...","origin":"...","confidence":0.8}],'
            '"actions":[{"action_type":"...","payload":{},"reason":"...","confidence":0.8}]}'
        )
        raw = self._client.complete(system=system, user=json.dumps(payload, ensure_ascii=False))
        data = json.loads(_extract_json(raw))
        principles = [
            LearnedPrincipleProposal(
                rule=item["rule"],
                origin=item.get("origin", "llm_reflection"),
                confidence=float(item.get("confidence", 0.75)),
            )
            for item in data.get("principles", [])
            if item.get("rule")
        ]
        actions = [
            EvolutionActionProposal(
                action_type=item["action_type"],
                payload=item.get("payload") or {},
                reason=item.get("reason", "llm_reflection"),
                confidence=float(item.get("confidence", 0.75)),
            )
            for item in data.get("actions", [])
            if item.get("action_type")
        ]
        return ReflectionResult(principles=principles, actions=actions)


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text
