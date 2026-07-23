"""SQLAlchemy-backed repository for semantic atoms/commitments/disclosure logs."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.memory.semantic.domain import (
    AtomKind,
    Commitment,
    CommitmentType,
    DisclosureLog,
    SemanticAtom,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
)
from assistant_platform.memory.semantic.models import (
    CommitmentRow,
    DisclosureLogRow,
    SemanticAtomRow,
)


def _row_to_atom(row: SemanticAtomRow) -> SemanticAtom:
    evidence = row.evidence_json or {}
    first = row.first_confirmed_at or row.created_at
    return SemanticAtom(
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
        first_confirmed_at=first,
        evidence_session_ids=tuple(evidence.get("session_ids") or ()),
        evidence_chunk_ids=tuple(evidence.get("chunk_ids") or ()),
        evidence_message_seqs=tuple(evidence.get("message_seqs") or ()),
    )


def _row_to_commitment(row: CommitmentRow) -> Commitment:
    evidence = row.evidence_json or {}
    return Commitment(
        id=row.id,
        namespace=row.namespace,
        counterparty_id=row.counterparty_id,
        type=CommitmentType(row.type),
        statement=row.statement,
        scope=row.scope or {},
        status=row.status,
        created_at=row.created_at,
        first_confirmed_at=row.first_confirmed_at or row.created_at,
        last_confirmed_at=row.last_confirmed_at or row.created_at,
        evidence_session_ids=tuple(evidence.get("session_ids") or ()),
        supersedes_id=row.supersedes_id,
    )


def _normalize_content(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class SemanticMemoryRepository:
    """CRUD + disclosure-log persistence for ``ap_semantic_atoms`` / ``ap_commitments``."""

    def __init__(self, session: Session):
        self.session = session

    def list_atoms(
        self,
        namespace: str,
        subject_ids: list[str],
        *,
        query: str | None = None,
    ) -> list[SemanticAtom]:
        stmt = select(SemanticAtomRow).where(
            SemanticAtomRow.namespace == namespace,
            SemanticAtomRow.status == "active",
        )
        if subject_ids:
            stmt = stmt.where(SemanticAtomRow.subject_id.in_(subject_ids))
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

    def find_similar_atom(
        self,
        namespace: str,
        subject_id: str,
        content: str,
    ) -> SemanticAtom | None:
        normalized = _normalize_content(content)
        stmt = select(SemanticAtomRow).where(
            SemanticAtomRow.namespace == namespace,
            SemanticAtomRow.subject_id == subject_id,
            SemanticAtomRow.status == "active",
        )
        for row in self.session.scalars(stmt).all():
            if _normalize_content(row.content) == normalized:
                return _row_to_atom(row)
        return None

    def upsert_atom(self, atom: SemanticAtom) -> SemanticAtom:
        evidence = {
            "session_ids": list(atom.evidence_session_ids),
            "chunk_ids": list(atom.evidence_chunk_ids),
            "message_seqs": list(atom.evidence_message_seqs),
        }
        first_confirmed = atom.first_confirmed_at or atom.created_at
        row = SemanticAtomRow(
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
            first_confirmed_at=first_confirmed,
            supersedes_id=atom.supersedes_id,
            status=atom.status,
            evidence_json=evidence,
            embedding_json=None,
        )
        self.session.add(row)
        self.session.flush()
        return _row_to_atom(row)

    def touch_atom(self, atom_id: str, seen_at: datetime) -> None:
        row = self.session.get(SemanticAtomRow, atom_id)
        if row:
            row.last_seen_at = seen_at

    def supersede_atom(self, old_id: str, new_atom: SemanticAtom) -> SemanticAtom:
        old_row = self.session.get(SemanticAtomRow, old_id)
        if old_row:
            old_row.status = "superseded"
        new_atom.supersedes_id = old_id
        return self.upsert_atom(new_atom)

    def add_commitment(self, commitment: Commitment) -> Commitment:
        evidence = {"session_ids": list(commitment.evidence_session_ids)}
        first = commitment.first_confirmed_at or commitment.created_at
        last = commitment.last_confirmed_at or commitment.created_at
        row = CommitmentRow(
            id=commitment.id or str(uuid.uuid4()),
            namespace=commitment.namespace,
            counterparty_id=commitment.counterparty_id,
            type=commitment.type.value,
            statement=commitment.statement,
            scope=commitment.scope,
            status=commitment.status,
            first_confirmed_at=first,
            last_confirmed_at=last,
            evidence_json=evidence,
            supersedes_id=commitment.supersedes_id,
            created_at=commitment.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return _row_to_commitment(row)

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

    def list_atoms_since(self, namespace: str, *, days: int = 7) -> list[SemanticAtom]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(SemanticAtomRow)
            .where(
                SemanticAtomRow.namespace == namespace,
                SemanticAtomRow.status == "active",
                SemanticAtomRow.created_at >= since,
            )
            .order_by(SemanticAtomRow.created_at.desc())
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

    def get_atom_embedding(self, atom_id: str) -> list[float] | None:
        row = self.session.get(SemanticAtomRow, atom_id)
        if row and row.embedding_json:
            return list(row.embedding_json)
        return None

    def save_atom_embedding(self, atom_id: str, vector: list[float]) -> None:
        row = self.session.get(SemanticAtomRow, atom_id)
        if row:
            row.embedding_json = vector
