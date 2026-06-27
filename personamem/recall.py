from __future__ import annotations

import logging

from personamem.domain import DisclosureResult, VisibilityContext
from personamem.gate import apply_disclosure_gate
from personamem.ports import MemoryRepository, Reviewer

logger = logging.getLogger(__name__)


def recall_memories(
    repo: MemoryRepository,
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
    principles = repo.list_principles(namespace)

    review = None
    if reviewer is not None:
        try:
            review = reviewer.review(
                namespace=namespace,
                context=context,
                query=query,
                atoms=atoms,
                commitments=commitments,
                principles=principles,
            )
        except Exception:
            logger.exception("Reviewer failed; falling back to deterministic gate only")

    released, blocked, deflection = apply_disclosure_gate(
        atoms=atoms,
        context=context,
        commitments=commitments,
        principles=principles,
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
