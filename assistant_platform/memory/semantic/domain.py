"""Domain types for assistant_platform semantic memory (facts/commitments).

Mirrors the shape of the legacy ``personamem.domain`` module (atom kind,
visibility, sensitivity, evidence/confidence/supersession fields) so ported
callers keep the same semantics without importing ``personamem``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AtomKind(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    EVENT = "event"


class SourceVisibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class CommitmentType(str, Enum):
    PROMISED = "promised"
    REFUSED = "refused"


class DeflectionReason(str, Enum):
    COMMITMENT = "commitment"
    PRIVACY_DEFAULT = "privacy_default"
    BOTTOM_LINE = "bottom_line"
    NONE = "none"


def team_id_to_namespace(team_id: str) -> str:
    """Map a Pulse team id to the semantic-memory namespace."""
    return f"team:{team_id}"


@dataclass(frozen=True)
class VisibilityContext:
    """Current conversation scene: public (group) or private to one audience."""

    visibility: SourceVisibility
    audience_id: str | None = None

    @classmethod
    def public(cls) -> VisibilityContext:
        return cls(visibility=SourceVisibility.PUBLIC)

    @classmethod
    def private(cls, audience_id: str) -> VisibilityContext:
        return cls(visibility=SourceVisibility.PRIVATE, audience_id=audience_id)

    def is_public(self) -> bool:
        return self.visibility == SourceVisibility.PUBLIC


@dataclass
class SemanticAtom:
    id: str
    namespace: str
    subject_id: str
    kind: AtomKind
    content: str
    source_visibility: SourceVisibility
    sensitivity: Sensitivity
    confidence: float
    created_at: datetime
    last_seen_at: datetime
    status: str = "active"
    supersedes_id: str | None = None
    first_confirmed_at: datetime | None = None
    evidence_session_ids: tuple[str, ...] = ()
    evidence_chunk_ids: tuple[str, ...] = ()
    evidence_message_seqs: tuple[int, ...] = ()


@dataclass
class Commitment:
    id: str
    namespace: str
    counterparty_id: str
    type: CommitmentType
    statement: str
    scope: dict[str, Any]
    status: str
    created_at: datetime
    first_confirmed_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    evidence_session_ids: tuple[str, ...] = ()
    supersedes_id: str | None = None


@dataclass
class DistilledAtom:
    kind: AtomKind
    content: str
    confidence: float = 1.0
    evidence_session_ids: tuple[str, ...] = ()
    evidence_chunk_ids: tuple[str, ...] = ()
    evidence_message_seqs: tuple[int, ...] = ()


@dataclass
class DistilledCommitment:
    counterparty_id: str
    type: CommitmentType
    statement: str
    scope: dict[str, Any] = field(default_factory=dict)
    evidence_session_ids: tuple[str, ...] = ()


@dataclass
class DistillResult:
    atoms: list[DistilledAtom] = field(default_factory=list)
    commitments: list[DistilledCommitment] = field(default_factory=list)


@dataclass
class ReviewDecision:
    release_ids: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    deflection_reason: DeflectionReason = DeflectionReason.NONE


@dataclass
class DisclosureResult:
    released_atoms: list[SemanticAtom]
    blocked_atoms: list[SemanticAtom]
    deflection_reason: DeflectionReason
    disclosure_id: str | None = None


@dataclass
class DisclosureLog:
    id: str
    namespace: str
    visibility: SourceVisibility
    audience_id: str | None
    query_excerpt: str
    released_atom_ids: list[str]
    blocked_atom_ids: list[str]
    deflection_reason: str
    created_at: datetime
