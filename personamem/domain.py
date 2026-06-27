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


class PrincipleTier(str, Enum):
    BOTTOM_LINE = "bottom_line"
    LEARNED = "learned"


class DeflectionReason(str, Enum):
    COMMITMENT = "commitment"
    PRIVACY_DEFAULT = "privacy_default"
    BOTTOM_LINE = "bottom_line"
    NONE = "none"


@dataclass(frozen=True)
class VisibilityContext:
  """当前对话场景：公开（群）或对某人的私聊。"""

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
class MemoryAtom:
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


@dataclass
class Principle:
    id: str
    namespace: str
    tier: PrincipleTier
    rule: str
    status: str
    created_at: datetime
    origin: str | None = None


@dataclass
class DistilledAtom:
    kind: AtomKind
    content: str
    confidence: float = 1.0


@dataclass
class DistilledCommitment:
    counterparty_id: str
    type: CommitmentType
    statement: str
    scope: dict[str, Any] = field(default_factory=dict)


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
    released_atoms: list[MemoryAtom]
    blocked_atoms: list[MemoryAtom]
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


@dataclass
class ConversationTurn:
    id: str
    namespace: str
    subject_id: str
    role: str  # user | assistant
    content: str
    visibility: SourceVisibility
    created_at: datetime


@dataclass
class EvolutionActionProposal:
    action_type: str
    payload: dict[str, Any]
    reason: str
    confidence: float = 0.8


@dataclass
class EvolutionActionResult:
    action_type: str
    status: str  # executed | skipped | failed
    detail: str = ""


@dataclass
class EvolutionResult:
    principles: list[Principle]
    actions: list[EvolutionActionResult]
