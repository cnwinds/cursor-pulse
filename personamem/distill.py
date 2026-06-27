from __future__ import annotations

import logging
import uuid
from datetime import datetime

from personamem.domain import (
    Commitment,
    CommitmentType,
    MemoryAtom,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
)
from personamem.ports import Clock, Distiller, MemoryRepository

logger = logging.getLogger(__name__)


def _default_sensitivity(context: VisibilityContext) -> Sensitivity:
    if context.is_public():
        return Sensitivity.PUBLIC
    return Sensitivity.CONFIDENTIAL


def distill_conversation(
    repo: MemoryRepository,
    distiller: Distiller,
    clock: Clock,
    *,
    namespace: str,
    subject_id: str,
    context: VisibilityContext,
    transcript: str,
) -> tuple[list[MemoryAtom], list[Commitment]]:
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

    now = clock.now()
    source_vis = SourceVisibility.PUBLIC if context.is_public() else SourceVisibility.PRIVATE
    default_sens = _default_sensitivity(context)
    saved_atoms: list[MemoryAtom] = []
    saved_commitments: list[Commitment] = []

    for item in result.atoms:
        existing = None
        if hasattr(repo, "find_similar_atom"):
            existing = repo.find_similar_atom(namespace, subject_id, item.content)  # type: ignore[attr-defined]

        if existing and existing.content.strip() == item.content.strip():
            repo.touch_atom(existing.id, now)
            saved_atoms.append(existing)
            continue

        atom = MemoryAtom(
            id=str(uuid.uuid4()),
            namespace=namespace,
            subject_id=subject_id,
            kind=item.kind,
            content=item.content,
            source_visibility=source_vis,
            sensitivity=default_sens,
            confidence=item.confidence,
            created_at=now,
            last_seen_at=now,
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
            created_at=now,
        )
        saved_commitments.append(repo.add_commitment(commitment))

    return saved_atoms, saved_commitments
