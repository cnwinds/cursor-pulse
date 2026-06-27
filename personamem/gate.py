from __future__ import annotations

from personamem.domain import (
    Commitment,
    CommitmentType,
    DeflectionReason,
    MemoryAtom,
    Principle,
    ReviewDecision,
    Sensitivity,
    VisibilityContext,
)

MIN_CONFIDENCE = 0.5


def _atom_blocked_by_commitment(atom: MemoryAtom, commitments: list[Commitment]) -> bool:
    for commitment in commitments:
        if commitment.status != "active":
            continue
        if commitment.type != CommitmentType.PROMISED:
            continue
        scope = commitment.scope or {}
        atom_ids = scope.get("atom_ids") or []
        if atom.id in atom_ids:
            return True
        keywords = scope.get("topic_keywords") or []
        content_lower = atom.content.lower()
        if any(kw.lower() in content_lower for kw in keywords):
            return True
        if commitment.counterparty_id == atom.subject_id and keywords:
            if any(kw.lower() in content_lower for kw in keywords):
                return True
    return False


def _deterministic_block(
    atom: MemoryAtom,
    context: VisibilityContext,
    commitments: list[Commitment],
) -> DeflectionReason | None:
    if atom.confidence < MIN_CONFIDENCE:
        return DeflectionReason.PRIVACY_DEFAULT

    if _atom_blocked_by_commitment(atom, commitments):
        return DeflectionReason.COMMITMENT

    if context.is_public():
        if atom.sensitivity != Sensitivity.PUBLIC:
            return DeflectionReason.PRIVACY_DEFAULT
        return None

    audience = context.audience_id
    if not audience:
        return DeflectionReason.PRIVACY_DEFAULT

    if atom.subject_id == audience:
        return None

    if atom.sensitivity != Sensitivity.PUBLIC:
        return DeflectionReason.PRIVACY_DEFAULT

    return None


def apply_disclosure_gate(
    *,
    atoms: list[MemoryAtom],
    context: VisibilityContext,
    commitments: list[Commitment],
    principles: list[Principle],
    review: ReviewDecision | None = None,
) -> tuple[list[MemoryAtom], list[MemoryAtom], DeflectionReason]:
    review_block_ids = set(review.block_ids if review else [])
    review_release_ids = set(review.release_ids if review else [])

    released: list[MemoryAtom] = []
    blocked: list[MemoryAtom] = []
    reasons: set[DeflectionReason] = set()

    for atom in atoms:
        if atom.id in review_block_ids:
            blocked.append(atom)
            reason = review.deflection_reason if review else DeflectionReason.PRIVACY_DEFAULT
            if reason != DeflectionReason.NONE:
                reasons.add(reason)
            continue

        det_reason = _deterministic_block(atom, context, commitments)
        if det_reason is not None:
            blocked.append(atom)
            reasons.add(det_reason)
            continue

        if review and review.release_ids and atom.id not in review_release_ids:
            blocked.append(atom)
            reasons.add(DeflectionReason.PRIVACY_DEFAULT)
            continue

        released.append(atom)

    if not blocked:
        deflection = DeflectionReason.NONE
    elif DeflectionReason.COMMITMENT in reasons:
        deflection = DeflectionReason.COMMITMENT
    elif DeflectionReason.BOTTOM_LINE in reasons:
        deflection = DeflectionReason.BOTTOM_LINE
    else:
        deflection = DeflectionReason.PRIVACY_DEFAULT

    if review and review.deflection_reason != DeflectionReason.NONE and blocked:
        if review.deflection_reason == DeflectionReason.BOTTOM_LINE:
            deflection = DeflectionReason.BOTTOM_LINE

    _ = principles  # reserved for reviewer; deterministic layer stays conservative
    return released, blocked, deflection
