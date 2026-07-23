"""Rule-based and LLM disclosure reviewers for semantic recall."""

from __future__ import annotations

import json
import re
from typing import Protocol

from assistant_platform.memory.semantic.domain import (
    DeflectionReason,
    ReviewDecision,
    Sensitivity,
    VisibilityContext,
)


class Reviewer(Protocol):
    def review(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query: str,
        atoms,
        commitments,
        principles=(),
    ) -> ReviewDecision: ...


class RuleBasedReviewer:
    """确定性审查器：公开场景不放行机密；可叠加承诺语义。"""

    def review(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query: str,
        atoms,
        commitments,
        principles=(),
    ) -> ReviewDecision:
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


class LlmReviewer:
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
        principles=(),
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
