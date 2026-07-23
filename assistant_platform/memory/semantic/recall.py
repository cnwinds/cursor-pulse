"""Semantic recall: gate atoms/commitments for the current disclosure context.

Ported from the legacy ``personamem.recall`` module; the optional LLM
``Reviewer`` hook is preserved for future use but every current caller in
assistant_platform passes ``reviewer=None`` (deterministic gate only).
"""

from __future__ import annotations

import logging
from typing import Protocol

from assistant_platform.memory.semantic.domain import DisclosureResult, VisibilityContext
from assistant_platform.memory.semantic.gate import apply_disclosure_gate
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository

logger = logging.getLogger(__name__)


class Reviewer(Protocol):
    def review(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query: str,
        atoms,
        commitments,
    ): ...


def recall_memories(
    repo: SemanticMemoryRepository,
    reviewer: Reviewer | None,
    *,
    namespace: str,
    subject_ids: list[str],
    context: VisibilityContext,
    query: str,
    log: bool = True,
) -> DisclosureResult:
    atoms = repo.list_atoms(namespace, subject_ids)
    commitments = repo.list_commitments(namespace, counterparty_ids=subject_ids or None)

    review = None
    if reviewer is not None:
        try:
            review = reviewer.review(
                namespace=namespace,
                context=context,
                query=query,
                atoms=atoms,
                commitments=commitments,
            )
        except Exception:
            logger.exception("Reviewer failed; falling back to deterministic gate only")

    released, blocked, deflection = apply_disclosure_gate(
        atoms=atoms,
        context=context,
        commitments=commitments,
        review=review,
    )

    disclosure_id = None
    if log:
        disclosure_id = repo.log_disclosure(
            namespace=namespace,
            context=context,
            query_excerpt=query,
            released_atom_ids=[a.id for a in released],
            blocked_atom_ids=[a.id for a in blocked],
            deflection_reason=deflection.value,
        )

    return DisclosureResult(
        released_atoms=released,
        blocked_atoms=blocked,
        deflection_reason=deflection,
        disclosure_id=disclosure_id,
    )
