"""Stable data contracts for chat archive recall and session summaries.

These types are shared by local SQLite backends and future pgvector/OpenSearch
adapters. Field names and semantics are part of the public contract — change
only with a documented migration / index version bump.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(str, Enum):
    """Isolation domain for archived and recalled content."""

    PERSONAL = "personal"
    GROUP = "group"


class MemorySourceType(str, Enum):
    """Origin layer of a recalled item."""

    ARCHIVE_CHUNK = "archive_chunk"
    FACT = "fact"
    COMMITMENT = "commitment"
    PREFERENCE = "preference"
    PROFILE = "profile"


class ArchivePipelineStatus(str, Enum):
    """End-to-end archive pipeline state for a closed session."""

    PENDING = "pending"
    PARTIAL = "partial"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class ArchivePipelineStage(str, Enum):
    """Individual idempotent stage within the close pipeline."""

    ARCHIVE = "archive"
    INDEX = "index"
    SUMMARY = "summary"
    FACTS = "facts"
    PROFILE = "profile"


class ProfileDimension(str, Enum):
    """Whitelist of observable interaction preferences (no sensitive traits)."""

    ADDRESSING = "addressing"
    LANGUAGE = "language"
    FORMALITY = "formality"
    VERBOSITY = "verbosity"
    STRUCTURE = "structure"
    EXAMPLES = "examples"
    PROACTIVITY = "proactivity"
    CONFIRMATION = "confirmation"
    DECISION_STYLE = "decision_style"
    DOMAIN_FAMILIARITY = "domain_familiarity"
    EXPLICIT_TABOO = "explicit_taboo"


class ChunkAnchor(BaseModel):
    """Stable pointer to an archive chunk for expand / read_range."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    chunk_index: int
    start_seq: int
    end_seq: int


class RecallCursor(BaseModel):
    """Opaque-stable pagination cursor for hybrid search results."""

    model_config = ConfigDict(frozen=True)

    query_fingerprint: str
    sort_key: str
    offset: int = 0


class SearchPageMeta(BaseModel):
    """Pagination envelope shared by search tools and RecallBundle."""

    model_config = ConfigDict(frozen=True)

    total_hits: int
    returned_count: int
    has_more: bool
    cursor: RecallCursor | None = None


class ArchiveHit(BaseModel):
    """Single retrievable memory fragment with positional metadata."""

    model_config = ConfigDict(frozen=True)

    memory_id: str
    session_id: str
    source_type: MemorySourceType
    scope: MemoryScope
    text: str
    source_roles: tuple[str, ...] = ()
    occurred_from: datetime
    occurred_to: datetime
    start_seq: int
    end_seq: int
    chunk_index: int
    session_message_total: int
    session_chunk_total: int
    rank: int
    score: float
    confidence: float | None = None
    has_prev: bool = False
    has_next: bool = False
    anchor: ChunkAnchor


class NeighborWindow(BaseModel):
    """Adjacent chunks around an anchor hit."""

    model_config = ConfigDict(frozen=True)

    anchor: ChunkAnchor
    prev_hits: tuple[ArchiveHit, ...] = ()
    next_hits: tuple[ArchiveHit, ...] = ()
    expand_count: int = 0


class SessionSummaryEvidence(BaseModel):
    """Evidence link from a summary item back to archive messages/chunks."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    chunk_id: str | None = None
    message_seq: int | None = None
    occurred_at: datetime | None = None
    confidence: float = 0.0


class SessionSummaryItem(BaseModel):
    """Structured summary line (fact, commitment, preference, etc.)."""

    model_config = ConfigDict(frozen=True)

    content: str
    kind: str
    confidence: float = 0.0
    evidence: tuple[SessionSummaryEvidence, ...] = ()


class SessionSummary(BaseModel):
    """Structured close-session summary with evidence-backed items."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    scope: MemoryScope
    subject_id: str
    team_id: str
    topic: str = ""
    user_goal: str = ""
    outcome: str = ""
    facts: tuple[SessionSummaryItem, ...] = ()
    commitments: tuple[SessionSummaryItem, ...] = ()
    open_items: tuple[str, ...] = ()
    preferences: tuple[SessionSummaryItem, ...] = ()
    narrative_summary: str = ""
    archived_at: datetime | None = None
    pipeline_status: ArchivePipelineStatus = ArchivePipelineStatus.PENDING


class FactRecallItem(BaseModel):
    """Personamem fact/commitment/preference surfaced in hybrid recall."""

    model_config = ConfigDict(frozen=True)

    memory_id: str
    source_type: MemorySourceType
    scope: MemoryScope
    subject_id: str
    content: str
    confidence: float
    first_confirmed_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    evidence_session_ids: tuple[str, ...] = ()
    rank: int = 0
    score: float = 0.0


class ProfileGuidanceItem(BaseModel):
    """Single compiled interaction preference for model injection."""

    model_config = ConfigDict(frozen=True)

    dimension: ProfileDimension
    guidance: str
    confidence: float
    explicit: bool = False


class ProfileGuidance(BaseModel):
    """Compressed interaction profile block (not psychological assessment)."""

    model_config = ConfigDict(frozen=True)

    subject_id: str
    team_id: str
    items: tuple[ProfileGuidanceItem, ...] = ()
    compiled_at: datetime | None = None


class RecallBundle(BaseModel):
    """Token-budgeted recall payload injected per user turn or returned by tools."""

    model_config = ConfigDict(frozen=True)

    fragments: tuple[ArchiveHit, ...] = ()
    facts: tuple[FactRecallItem, ...] = ()
    profile: ProfileGuidance | None = None
    page: SearchPageMeta = Field(
        default_factory=lambda: SearchPageMeta(
            total_hits=0,
            returned_count=0,
            has_more=False,
        )
    )
    token_estimate: int = 0
    recall_sources: tuple[str, ...] = ()
    built_at: datetime | None = None
    degraded: bool = False
    degrade_reason: str | None = None


class ArchiveStageStatus(BaseModel):
    """Per-stage status for observability and idempotent retries."""

    model_config = ConfigDict(frozen=True)

    stage: ArchivePipelineStage
    status: ArchivePipelineStatus
    attempt_count: int = 0
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)
