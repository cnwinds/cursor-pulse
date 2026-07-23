"""Rule-based / LLM fact distillers and the shared distill-and-persist flow.

Ported from ``pulse/memory_adapter/llm.py`` + ``personamem/distill.py`` with no
``personamem`` dependency; domain types come from
``assistant_platform.memory.semantic.domain``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Protocol

from assistant_platform.memory.semantic.domain import (
    AtomKind,
    Commitment,
    CommitmentType,
    DistilledAtom,
    DistilledCommitment,
    DistillResult,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
)
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository

logger = logging.getLogger(__name__)


class Distiller(Protocol):
    def distill(
        self,
        *,
        namespace: str,
        subject_id: str,
        context: VisibilityContext,
        transcript: str,
    ) -> DistillResult: ...


class RuleBasedDistiller:
    """无 LLM 的规则提炼器，用于默认接入与测试。"""

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


class LlmDistiller:
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


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _default_sensitivity(context: VisibilityContext) -> Sensitivity:
    if context.is_public():
        return Sensitivity.PUBLIC
    return Sensitivity.CONFIDENTIAL


def distill_conversation(
    repo: SemanticMemoryRepository,
    distiller: Distiller,
    *,
    namespace: str,
    subject_id: str,
    context: VisibilityContext,
    transcript: str,
    now: datetime | None = None,
) -> tuple[list, list]:
    """Distill a transcript and persist new/updated atoms + commitments."""
    if not transcript.strip():
        return [], []

    try:
        result = distiller.distill(
            namespace=namespace,
            subject_id=subject_id,
            context=context,
            transcript=transcript,
        )
    except Exception:
        logger.exception("Distiller failed; skipping memory write")
        return [], []

    resolved_now = now or datetime.now(timezone.utc)
    source_vis = SourceVisibility.PUBLIC if context.is_public() else SourceVisibility.PRIVATE
    default_sens = _default_sensitivity(context)
    saved_atoms = []
    saved_commitments = []

    for item in result.atoms:
        existing = repo.find_similar_atom(namespace, subject_id, item.content)
        if existing and existing.content.strip() == item.content.strip():
            repo.touch_atom(existing.id, resolved_now)
            saved_atoms.append(existing)
            continue

        from assistant_platform.memory.semantic.domain import SemanticAtom

        atom = SemanticAtom(
            id=str(uuid.uuid4()),
            namespace=namespace,
            subject_id=subject_id,
            kind=item.kind,
            content=item.content,
            source_visibility=source_vis,
            sensitivity=default_sens,
            confidence=item.confidence,
            created_at=resolved_now,
            last_seen_at=resolved_now,
            first_confirmed_at=resolved_now,
            evidence_session_ids=tuple(item.evidence_session_ids),
            evidence_chunk_ids=tuple(item.evidence_chunk_ids),
            evidence_message_seqs=tuple(item.evidence_message_seqs),
        )
        if existing:
            saved = repo.supersede_atom(existing.id, atom)
        else:
            saved = repo.upsert_atom(atom)
        saved_atoms.append(saved)

    for item in result.commitments:
        commitment = Commitment(
            id=str(uuid.uuid4()),
            namespace=namespace,
            counterparty_id=item.counterparty_id,
            type=item.type,
            statement=item.statement,
            scope=item.scope,
            status="active",
            created_at=resolved_now,
            first_confirmed_at=resolved_now,
            last_confirmed_at=resolved_now,
            evidence_session_ids=tuple(item.evidence_session_ids),
        )
        saved_commitments.append(repo.add_commitment(commitment))

    return saved_atoms, saved_commitments
