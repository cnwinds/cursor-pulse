from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from personamem.domain import (
    AtomKind,
    Commitment,
    CommitmentType,
    ConversationTurn,
    DisclosureLog,
    MemoryAtom,
    Principle,
    PrincipleTier,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
)
from personamem.models import (
    CommitmentRow,
    ConversationTurnRow,
    DisclosureLogRow,
    EvolutionActionLogRow,
    MemoryAtomRow,
    PrincipleRow,
)


def _row_to_atom(row: MemoryAtomRow) -> MemoryAtom:
    return MemoryAtom(
        id=row.id,
        namespace=row.namespace,
        subject_id=row.subject_id,
        kind=AtomKind(row.kind),
        content=row.content,
        source_visibility=SourceVisibility(row.source_visibility),
        sensitivity=Sensitivity(row.sensitivity),
        confidence=row.confidence,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        status=row.status,
        supersedes_id=row.supersedes_id,
    )


def _row_to_commitment(row: CommitmentRow) -> Commitment:
    return Commitment(
        id=row.id,
        namespace=row.namespace,
        counterparty_id=row.counterparty_id,
        type=CommitmentType(row.type),
        statement=row.statement,
        scope=row.scope or {},
        status=row.status,
        created_at=row.created_at,
    )


def _row_to_principle(row: PrincipleRow) -> Principle:
    return Principle(
        id=row.id,
        namespace=row.namespace,
        tier=PrincipleTier(row.tier),
        rule=row.rule,
        status=row.status,
        created_at=row.created_at,
        origin=row.origin,
    )


def _normalize_content(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class SqlAlchemyMemoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_atoms(
        self,
        namespace: str,
        subject_ids: list[str],
        *,
        query: str | None = None,
    ) -> list[MemoryAtom]:
        stmt = select(MemoryAtomRow).where(
            MemoryAtomRow.namespace == namespace,
            MemoryAtomRow.status == "active",
        )
        if subject_ids:
            stmt = stmt.where(MemoryAtomRow.subject_id.in_(subject_ids))
        rows = self.session.scalars(stmt).all()
        atoms = [_row_to_atom(row) for row in rows]
        if not query:
            return atoms
        tokens = [t for t in re.split(r"\s+", query.strip()) if t]
        if not tokens:
            return atoms
        return [
            atom
            for atom in atoms
            if any(token.lower() in atom.content.lower() for token in tokens)
        ]

    def list_commitments(
        self,
        namespace: str,
        counterparty_ids: list[str] | None = None,
    ) -> list[Commitment]:
        stmt = select(CommitmentRow).where(
            CommitmentRow.namespace == namespace,
            CommitmentRow.status == "active",
        )
        if counterparty_ids:
            stmt = stmt.where(CommitmentRow.counterparty_id.in_(counterparty_ids))
        return [_row_to_commitment(row) for row in self.session.scalars(stmt).all()]

    def list_principles(self, namespace: str) -> list[Principle]:
        stmt = select(PrincipleRow).where(
            PrincipleRow.namespace == namespace,
            PrincipleRow.status == "active",
        )
        return [_row_to_principle(row) for row in self.session.scalars(stmt).all()]

    def find_similar_atom(
        self,
        namespace: str,
        subject_id: str,
        content: str,
    ) -> MemoryAtom | None:
        normalized = _normalize_content(content)
        stmt = select(MemoryAtomRow).where(
            MemoryAtomRow.namespace == namespace,
            MemoryAtomRow.subject_id == subject_id,
            MemoryAtomRow.status == "active",
        )
        for row in self.session.scalars(stmt).all():
            if _normalize_content(row.content) == normalized:
                return _row_to_atom(row)
        return None

    def upsert_atom(self, atom: MemoryAtom) -> MemoryAtom:
        row = MemoryAtomRow(
            id=atom.id or str(uuid.uuid4()),
            namespace=atom.namespace,
            subject_id=atom.subject_id,
            kind=atom.kind.value,
            content=atom.content,
            source_visibility=atom.source_visibility.value,
            sensitivity=atom.sensitivity.value,
            confidence=atom.confidence,
            created_at=atom.created_at,
            last_seen_at=atom.last_seen_at,
            supersedes_id=atom.supersedes_id,
            status=atom.status,
            embedding_json=None,
        )
        self.session.add(row)
        self.session.flush()
        return _row_to_atom(row)

    def touch_atom(self, atom_id: str, seen_at: datetime) -> None:
        row = self.session.get(MemoryAtomRow, atom_id)
        if row:
            row.last_seen_at = seen_at

    def supersede_atom(self, old_id: str, new_atom: MemoryAtom) -> MemoryAtom:
        old_row = self.session.get(MemoryAtomRow, old_id)
        if old_row:
            old_row.status = "superseded"
        new_atom.supersedes_id = old_id
        return self.upsert_atom(new_atom)

    def add_commitment(self, commitment: Commitment) -> Commitment:
        row = CommitmentRow(
            id=commitment.id or str(uuid.uuid4()),
            namespace=commitment.namespace,
            counterparty_id=commitment.counterparty_id,
            type=commitment.type.value,
            statement=commitment.statement,
            scope=commitment.scope,
            status=commitment.status,
            created_at=commitment.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return _row_to_commitment(row)

    def add_principle(self, principle: Principle) -> Principle:
        row = PrincipleRow(
            id=principle.id or str(uuid.uuid4()),
            namespace=principle.namespace,
            tier=principle.tier.value,
            rule=principle.rule,
            origin=principle.origin,
            status=principle.status,
            created_at=principle.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return _row_to_principle(row)

    def log_disclosure(
        self,
        *,
        namespace: str,
        context: VisibilityContext,
        query_excerpt: str,
        released_atom_ids: list[str],
        blocked_atom_ids: list[str],
        deflection_reason: str,
    ) -> str:
        row = DisclosureLogRow(
            namespace=namespace,
            visibility=context.visibility.value,
            audience_id=context.audience_id,
            query_excerpt=query_excerpt[:500],
            released_atom_ids=released_atom_ids,
            blocked_atom_ids=blocked_atom_ids,
            deflection_reason=deflection_reason,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_atoms_since(self, namespace: str, *, days: int = 7) -> list[MemoryAtom]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(MemoryAtomRow)
            .where(
                MemoryAtomRow.namespace == namespace,
                MemoryAtomRow.status == "active",
                MemoryAtomRow.created_at >= since,
            )
            .order_by(MemoryAtomRow.created_at.desc())
        )
        return [_row_to_atom(row) for row in self.session.scalars(stmt).all()]

    def list_disclosure_logs(self, namespace: str, *, limit: int = 30) -> list[DisclosureLog]:
        stmt = (
            select(DisclosureLogRow)
            .where(DisclosureLogRow.namespace == namespace)
            .order_by(DisclosureLogRow.created_at.desc())
            .limit(limit)
        )
        logs: list[DisclosureLog] = []
        for row in self.session.scalars(stmt).all():
            logs.append(
                DisclosureLog(
                    id=row.id,
                    namespace=row.namespace,
                    visibility=SourceVisibility(row.visibility),
                    audience_id=row.audience_id,
                    query_excerpt=row.query_excerpt,
                    released_atom_ids=row.released_atom_ids or [],
                    blocked_atom_ids=row.blocked_atom_ids or [],
                    deflection_reason=row.deflection_reason,
                    created_at=row.created_at,
                )
            )
        return logs

    def list_evolution_actions(self, namespace: str, *, limit: int = 50) -> list[dict]:
        stmt = (
            select(EvolutionActionLogRow)
            .where(EvolutionActionLogRow.namespace == namespace)
            .order_by(EvolutionActionLogRow.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "action_type": row.action_type,
                "payload": row.payload or {},
                "status": row.status,
                "detail": row.detail,
                "created_at": row.created_at,
            }
            for row in self.session.scalars(stmt).all()
        ]

    def get_atom_embedding(self, atom_id: str) -> list[float] | None:
        row = self.session.get(MemoryAtomRow, atom_id)
        if row and row.embedding_json:
            return list(row.embedding_json)
        return None

    def save_atom_embedding(self, atom_id: str, vector: list[float]) -> None:
        row = self.session.get(MemoryAtomRow, atom_id)
        if row:
            row.embedding_json = vector

    def append_turn(
        self,
        *,
        namespace: str,
        subject_id: str,
        role: str,
        content: str,
        visibility: SourceVisibility,
        created_at: datetime,
    ) -> ConversationTurn:
        row = ConversationTurnRow(
            namespace=namespace,
            subject_id=subject_id,
            role=role,
            content=content,
            visibility=visibility.value,
            created_at=created_at,
        )
        self.session.add(row)
        self.session.flush()
        return ConversationTurn(
            id=row.id,
            namespace=row.namespace,
            subject_id=row.subject_id,
            role=row.role,
            content=row.content,
            visibility=SourceVisibility(row.visibility),
            created_at=row.created_at,
        )

    def list_recent_turns(
        self,
        namespace: str,
        subject_id: str,
        *,
        limit: int = 10,
    ) -> list[ConversationTurn]:
        stmt = (
            select(ConversationTurnRow)
            .where(
                ConversationTurnRow.namespace == namespace,
                ConversationTurnRow.subject_id == subject_id,
            )
            .order_by(ConversationTurnRow.created_at.desc())
            .limit(limit)
        )
        turns = [
            ConversationTurn(
                id=row.id,
                namespace=row.namespace,
                subject_id=row.subject_id,
                role=row.role,
                content=row.content,
                visibility=SourceVisibility(row.visibility),
                created_at=row.created_at,
            )
            for row in self.session.scalars(stmt).all()
        ]
        turns.reverse()
        return turns

    def prune_turns(self, namespace: str, subject_id: str, *, keep: int = 20) -> int:
        stmt = (
            select(ConversationTurnRow)
            .where(
                ConversationTurnRow.namespace == namespace,
                ConversationTurnRow.subject_id == subject_id,
            )
            .order_by(ConversationTurnRow.created_at.desc())
        )
        rows = list(self.session.scalars(stmt).all())
        removed = 0
        for row in rows[keep:]:
            self.session.delete(row)
            removed += 1
        return removed

    def log_evolution_action(
        self,
        *,
        namespace: str,
        action_type: str,
        payload: dict,
        status: str,
        detail: str = "",
    ) -> str:
        row = EvolutionActionLogRow(
            namespace=namespace,
            action_type=action_type,
            payload=payload,
            status=status,
            detail=detail,
        )
        self.session.add(row)
        self.session.flush()
        return row.id
