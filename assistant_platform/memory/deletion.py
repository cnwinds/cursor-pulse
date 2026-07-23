"""Cascade memory deletion: archive, FTS, vectors, summaries, facts, profile signals."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant_platform.memory.archive_indexer import purge_session_index
from assistant_platform.memory.archive_models import MemoryScope, SessionArchiveRow
from assistant_platform.memory.session_summary import SessionSummaryRow
from assistant_platform.profiles.compiler import compile_and_persist_effective_profile
from assistant_platform.profiles.models import ProfileEffectiveRow, ProfileSignalRow
from assistant_platform.memory.semantic.models import CommitmentRow, SemanticAtomRow
from assistant_platform.memory.semantic.domain import team_id_to_namespace


@dataclass
class SessionMemoryDeletionResult:
    session_id: str
    archives_removed: int = 0
    summaries_removed: int = 0
    atoms_removed: int = 0
    commitments_removed: int = 0
    profile_signals_updated: int = 0
    profile_signals_revoked: int = 0
    profiles_recompiled: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class PersonalMemoryDeletionResult:
    user_id: str
    team_id: str
    sessions_processed: int = 0
    atoms_removed: int = 0
    profile_signals_removed: int = 0
    effective_profiles_removed: int = 0


def _evidence_references_session(evidence_json: dict | None, session_id: str) -> bool:
    session_ids = (evidence_json or {}).get("session_ids") or []
    return session_id in session_ids


def _purge_semantic_memory_for_session(
    session: Session,
    *,
    namespace: str,
    session_id: str,
) -> tuple[int, int]:
    atoms_removed = 0
    for row in session.scalars(
        select(SemanticAtomRow).where(SemanticAtomRow.namespace == namespace)
    ).all():
        if _evidence_references_session(row.evidence_json, session_id):
            session.delete(row)
            atoms_removed += 1

    commitments_removed = 0
    for row in session.scalars(
        select(CommitmentRow).where(CommitmentRow.namespace == namespace)
    ).all():
        if _evidence_references_session(row.evidence_json, session_id):
            session.delete(row)
            commitments_removed += 1

    return atoms_removed, commitments_removed


def _revoke_profile_signals_for_session(
    session: Session,
    *,
    session_id: str,
) -> tuple[int, int, set[tuple[str, str]]]:
    updated = 0
    revoked = 0
    recompile_targets: set[tuple[str, str]] = set()
    for signal in session.scalars(select(ProfileSignalRow)).all():
        source_ids = list(signal.source_session_ids_json or [])
        if session_id not in source_ids:
            continue
        recompile_targets.add((signal.user_id, signal.team_id))
        remaining = [sid for sid in source_ids if sid != session_id]
        signal.source_session_ids_json = remaining
        if not remaining:
            signal.status = "revoked"
            revoked += 1
        else:
            updated += 1
        session.add(signal)
    return updated, revoked, recompile_targets


def purge_session_memory(
    session: Session,
    session_id: str,
    *,
    team_id: str | None = None,
    recompile_profiles: bool = True,
) -> SessionMemoryDeletionResult:
    """Remove all derived memory for one closed session."""
    result = SessionMemoryDeletionResult(session_id=session_id)
    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)
    )
    if archive is None and team_id is None:
        return result
    effective_team = (archive.team_id if archive else team_id) or ""
    namespace = team_id_to_namespace(effective_team)

    purge_session_index(session, session_id)

    summary = session.scalar(
        select(SessionSummaryRow).where(SessionSummaryRow.session_id == session_id)
    )
    if summary is not None:
        session.delete(summary)
        result.summaries_removed = 1

    if archive is not None:
        session.delete(archive)
        result.archives_removed = 1

    atoms, commitments = _purge_semantic_memory_for_session(
        session, namespace=namespace, session_id=session_id
    )
    result.atoms_removed = atoms
    result.commitments_removed = commitments

    updated, revoked, targets = _revoke_profile_signals_for_session(session, session_id=session_id)
    result.profile_signals_updated = updated
    result.profile_signals_revoked = revoked

    if recompile_profiles:
        for user_id, tid in targets:
            compile_and_persist_effective_profile(session, user_id=user_id, team_id=tid)
            result.profiles_recompiled.add((user_id, tid))

    session.flush()
    return result


def purge_memory_atom(
    session: Session,
    atom_id: str,
    *,
    namespace: str,
    subject_id: str,
) -> bool:
    row = session.get(SemanticAtomRow, atom_id)
    if row is None:
        return False
    if row.namespace != namespace or row.subject_id != subject_id:
        return False
    session.delete(row)
    session.flush()
    return True


def purge_all_personal_memory(
    session: Session,
    *,
    user_id: str,
    team_id: str,
) -> PersonalMemoryDeletionResult:
    """Delete all personal-scope archives and derived data for a user within a team."""
    result = PersonalMemoryDeletionResult(user_id=user_id, team_id=team_id)
    namespace = team_id_to_namespace(team_id)

    archives = session.scalars(
        select(SessionArchiveRow).where(
            SessionArchiveRow.team_id == team_id,
            SessionArchiveRow.subject_id == user_id,
            SessionArchiveRow.scope == MemoryScope.PERSONAL.value,
        )
    ).all()
    for archive in archives:
        purge_session_memory(
            session,
            archive.session_id,
            team_id=team_id,
            recompile_profiles=False,
        )
        result.sessions_processed += 1

    for row in session.scalars(
        select(SemanticAtomRow).where(
            SemanticAtomRow.namespace == namespace,
            SemanticAtomRow.subject_id == user_id,
        )
    ).all():
        session.delete(row)
        result.atoms_removed += 1

    for row in session.scalars(
        select(CommitmentRow).where(
            CommitmentRow.namespace == namespace,
            CommitmentRow.counterparty_id == user_id,
        )
    ).all():
        session.delete(row)

    signals = session.scalars(
        select(ProfileSignalRow).where(
            ProfileSignalRow.user_id == user_id,
            ProfileSignalRow.team_id == team_id,
        )
    ).all()
    for signal in signals:
        session.delete(signal)
        result.profile_signals_removed += 1

    effective = session.scalar(
        select(ProfileEffectiveRow).where(
            ProfileEffectiveRow.user_id == user_id,
            ProfileEffectiveRow.team_id == team_id,
        )
    )
    if effective is not None:
        session.delete(effective)
        result.effective_profiles_removed = 1

    session.flush()
    return result
