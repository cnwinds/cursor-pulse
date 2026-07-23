"""Self-contained semantic memory (facts/commitments/disclosure) for assistant_platform.

Replaces the legacy ``personamem`` package: atoms and commitments live in
``ap_semantic_atoms`` / ``ap_commitments`` (not ``pm_*``), with the same
evidence/confidence/supersession/visibility semantics but no external
dependency. See ``docs/requirements/chat-memory-web-search.md`` §3.3.
"""

from __future__ import annotations

from assistant_platform.memory.semantic.domain import (
    AtomKind,
    Commitment,
    CommitmentType,
    DeflectionReason,
    DisclosureLog,
    DisclosureResult,
    DistilledAtom,
    DistilledCommitment,
    DistillResult,
    ReviewDecision,
    SemanticAtom,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
    team_id_to_namespace,
)
from assistant_platform.memory.semantic.distill import (
    LlmDistiller,
    RuleBasedDistiller,
    distill_conversation,
)
from assistant_platform.memory.semantic.gate import apply_disclosure_gate
from assistant_platform.memory.semantic.recall import recall_memories
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository

__all__ = [
    "AtomKind",
    "Commitment",
    "CommitmentType",
    "DeflectionReason",
    "DisclosureLog",
    "DisclosureResult",
    "DistilledAtom",
    "DistilledCommitment",
    "DistillResult",
    "LlmDistiller",
    "ReviewDecision",
    "RuleBasedDistiller",
    "SemanticAtom",
    "SemanticMemoryRepository",
    "Sensitivity",
    "SourceVisibility",
    "VisibilityContext",
    "apply_disclosure_gate",
    "distill_conversation",
    "recall_memories",
    "team_id_to_namespace",
]
