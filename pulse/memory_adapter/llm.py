from __future__ import annotations

import json
import re

from personamem.domain import (
    AtomKind,
    CommitmentType,
    DeflectionReason,
    DistillResult,
    DistilledAtom,
    DistilledCommitment,
    ReviewDecision,
    VisibilityContext,
)
from personamem.ports import Distiller, Reviewer


class RuleBasedDistiller(Distiller):
    """无 LLM 的规则提炼器，用于 Pulse 默认接入与测试。"""

    def distill(
        self,
        *,
        namespace: str,
        subject_id: str,
        context: VisibilityContext,
        transcript: str,
    ) -> DistillResult:
        atoms: list[DistilledAtom] = []
        commitments: list[DistilledCommitment] = []

        for line in transcript.splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("偏好:"):
                atoms.append(
                    DistilledAtom(kind=AtomKind.PREFERENCE, content=text.removeprefix("偏好:").strip())
                )
            elif text.startswith("事实:"):
                atoms.append(DistilledAtom(kind=AtomKind.FACT, content=text.removeprefix("事实:").strip()))
            else:
                atoms.append(DistilledAtom(kind=AtomKind.FACT, content=text))

        if re.search(r"(?:行[,，]?)?我(?:答应)?不(?:在群里)?说", transcript):
            commitments.append(
                DistilledCommitment(
                    counterparty_id=subject_id,
                    type=CommitmentType.PROMISED,
                    statement="不在群里透露本次私聊敏感内容",
                    scope={"topic_keywords": ["Opus", "用量"]},
                )
            )

        return DistillResult(atoms=atoms, commitments=commitments)


class RuleBasedReviewer(Reviewer):
    """确定性审查器：公开场景不放行机密；可叠加承诺语义。"""

    def review(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query: str,
        atoms,
        commitments,
        principles,
    ) -> ReviewDecision:
        from personamem.domain import Sensitivity

        block_ids: list[str] = []
        release_ids: list[str] = []
        reason = DeflectionReason.NONE

        for atom in atoms:
            if context.is_public() and atom.sensitivity != Sensitivity.PUBLIC:
                block_ids.append(atom.id)
                reason = DeflectionReason.PRIVACY_DEFAULT
            else:
                release_ids.append(atom.id)

        for commitment in commitments:
            if commitment.status != "active":
                continue
            keywords = (commitment.scope or {}).get("topic_keywords") or []
            if context.is_public() and keywords:
                for atom in atoms:
                    if any(kw.lower() in atom.content.lower() for kw in keywords):
                        if atom.id not in block_ids:
                            block_ids.append(atom.id)
                        reason = DeflectionReason.COMMITMENT

        release_ids = [aid for aid in release_ids if aid not in block_ids]

        return ReviewDecision(
            release_ids=release_ids,
            block_ids=block_ids,
            deflection_reason=reason,
        )


class LlmDistiller(Distiller):
    def __init__(self, client):
        self._client = client

    def distill(
        self,
        *,
        namespace: str,
        subject_id: str,
        context: VisibilityContext,
        transcript: str,
    ) -> DistillResult:
        system = (
            "你是记忆提炼器。从对话中抽取 fact/preference/event 要点，以及 bot 对用户的承诺。"
            "只输出 JSON："
            '{"atoms":[{"kind":"fact|preference|event","content":"...","confidence":0.0-1.0}],'
            '"commitments":[{"type":"promised|refused","statement":"...","scope":{}}]}'
        )
        user = f"subject_id={subject_id}\nvisibility={context.visibility.value}\n\n{transcript}"
        raw = self._client.complete(system=system, user=user)
        data = json.loads(_extract_json(raw))
        atoms = [
            DistilledAtom(
                kind=AtomKind(item["kind"]),
                content=item["content"],
                confidence=float(item.get("confidence", 0.8)),
            )
            for item in data.get("atoms", [])
        ]
        commitments = [
            DistilledCommitment(
                counterparty_id=subject_id,
                type=CommitmentType(item["type"]),
                statement=item["statement"],
                scope=item.get("scope") or {},
            )
            for item in data.get("commitments", [])
        ]
        return DistillResult(atoms=atoms, commitments=commitments)


class LlmReviewer(Reviewer):
    def __init__(self, client):
        self._client = client

    def review(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query: str,
        atoms,
        commitments,
        principles,
    ) -> ReviewDecision:
        payload = {
            "context": {
                "visibility": context.visibility.value,
                "audience_id": context.audience_id,
            },
            "query": query,
            "atoms": [
                {
                    "id": a.id,
                    "subject_id": a.subject_id,
                    "content": a.content,
                    "sensitivity": a.sensitivity.value,
                }
                for a in atoms
            ],
            "commitments": [{"id": c.id, "statement": c.statement, "scope": c.scope} for c in commitments],
            "principles": [{"tier": p.tier.value, "rule": p.rule} for p in principles],
        }
        system = (
            "你是披露审查员。决定哪些 memory atom 可在当前场景披露。"
            '输出 JSON：{"release_ids":[],"block_ids":[],"deflection_reason":"commitment|privacy_default|bottom_line|none"}'
        )
        raw = self._client.complete(system=system, user=json.dumps(payload, ensure_ascii=False))
        data = json.loads(_extract_json(raw))
        return ReviewDecision(
            release_ids=data.get("release_ids") or [],
            block_ids=data.get("block_ids") or [],
            deflection_reason=DeflectionReason(data.get("deflection_reason", "none")),
        )


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text
