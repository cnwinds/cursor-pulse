from __future__ import annotations

from datetime import datetime
from typing import Protocol

from personamem.domain import (
    Commitment,
    ConversationTurn,
    DisclosureLog,
    DisclosureResult,
    DistillResult,
    EvolutionActionProposal,
    MemoryAtom,
    Principle,
    ReviewDecision,
    SourceVisibility,
    VisibilityContext,
)
from personamem.evolution import ActionExecutor, Reflector, ReflectionResult
from personamem.persona import Persona


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        from datetime import timezone

        return datetime.now(timezone.utc)


class Distiller(Protocol):
    def distill(
        self,
        *,
        namespace: str,
        subject_id: str,
        context: VisibilityContext,
        transcript: str,
    ) -> DistillResult: ...


class Reviewer(Protocol):
    def review(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query: str,
        atoms: list[MemoryAtom],
        commitments: list[Commitment],
        principles: list[Principle],
    ) -> ReviewDecision: ...


class MemoryRepository(Protocol):
    def list_atoms(
        self,
        namespace: str,
        subject_ids: list[str],
        *,
        query: str | None = None,
    ) -> list[MemoryAtom]: ...

    def list_commitments(
        self,
        namespace: str,
        counterparty_ids: list[str] | None = None,
    ) -> list[Commitment]: ...

    def list_principles(self, namespace: str) -> list[Principle]: ...

    def upsert_atom(self, atom: MemoryAtom) -> MemoryAtom: ...

    def touch_atom(self, atom_id: str, seen_at: datetime) -> None: ...

    def supersede_atom(self, old_id: str, new_atom: MemoryAtom) -> MemoryAtom: ...

    def add_commitment(self, commitment: Commitment) -> Commitment: ...

    def add_principle(self, principle: Principle) -> Principle: ...

    def log_disclosure(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query_excerpt: str,
        released_atom_ids: list[str],
        blocked_atom_ids: list[str],
        deflection_reason: str,
    ) -> str: ...

    def list_atoms_since(self, namespace: str, *, days: int = 7) -> list[MemoryAtom]: ...

    def list_disclosure_logs(self, namespace: str, *, limit: int = 30) -> list[DisclosureLog]: ...

    def list_evolution_actions(self, namespace: str, *, limit: int = 50) -> list[dict]: ...

    def get_atom_embedding(self, atom_id: str) -> list[float] | None: ...

    def save_atom_embedding(self, atom_id: str, vector: list[float]) -> None: ...

    def append_turn(
        self,
        *,
        namespace: str,
        subject_id: str,
        role: str,
        content: str,
        visibility: SourceVisibility,
        created_at: datetime,
    ) -> ConversationTurn: ...

    def list_recent_turns(
        self,
        namespace: str,
        subject_id: str,
        *,
        limit: int = 10,
    ) -> list[ConversationTurn]: ...

    def prune_turns(self, namespace: str, subject_id: str, *, keep: int = 20) -> int: ...

    def log_evolution_action(
        self,
        *,
        namespace: str,
        action_type: str,
        payload: dict,
        status: str,
        detail: str = "",
    ) -> str: ...


class Responder(Protocol):
    def reply(
        self,
        *,
        persona: Persona,
        user_message: str,
        display_name: str,
        disclosure: DisclosureResult,
        ranked_atoms: list[MemoryAtom],
        is_group: bool,
        recent_turns: list[ConversationTurn] | None = None,
        schedule_note: str | None = None,
        now: datetime | None = None,
    ) -> str: ...


__all__ = [
    "ActionExecutor",
    "Clock",
    "Distiller",
    "MemoryRepository",
    "Reflector",
    "ReflectionResult",
    "Responder",
    "Reviewer",
    "SystemClock",
]
