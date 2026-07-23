"""Compile effective interaction profile from signals and user corrections."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.memory.contracts import ProfileDimension, ProfileGuidance, ProfileGuidanceItem
from assistant_platform.profiles.models import ProfileCorrectionRow, ProfileEffectiveRow, ProfileSignalRow

_EXPLICITNESS_RANK = {"correction": 4, "explicit": 3, "inferred": 1}
_STATUS_ACTIVE = "active"


def _rank_signal(
    signal: ProfileSignalRow,
    *,
    corrected_dimensions: set[str],
) -> tuple[int, float, datetime]:
    if signal.dimension in corrected_dimensions:
        explicitness = "correction"
    else:
        explicitness = signal.explicitness or "inferred"
    created = signal.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (
        _EXPLICITNESS_RANK.get(explicitness, 0),
        float(signal.confidence or 0.0),
        created or datetime.min.replace(tzinfo=timezone.utc),
    )


def compile_profile_guidance(
    session: Session,
    *,
    user_id: str,
    team_id: str,
) -> ProfileGuidance:
    now = datetime.now(timezone.utc)
    signals = session.scalars(
        select(ProfileSignalRow).where(
            ProfileSignalRow.user_id == user_id,
            ProfileSignalRow.team_id == team_id,
            ProfileSignalRow.status == _STATUS_ACTIVE,
        )
    ).all()
    corrections = session.scalars(
        select(ProfileCorrectionRow).where(
            ProfileCorrectionRow.user_id == user_id,
            ProfileCorrectionRow.team_id == team_id,
        )
    ).all()
    correction_by_dimension: dict[str, ProfileCorrectionRow] = {}
    for correction in corrections:
        dimension = correction.dimension or ""
        if not dimension:
            signal = session.get(ProfileSignalRow, correction.signal_id)
            dimension = (signal.dimension if signal else "") or ProfileDimension.VERBOSITY.value
        correction_by_dimension[dimension] = correction

    winners: dict[str, ProfileGuidanceItem] = {}
    winner_rank: dict[str, tuple[int, float, datetime]] = {}
    corrected_dims = set(correction_by_dimension)

    for signal in signals:
        dimension = signal.dimension or ProfileDimension.VERBOSITY.value
        if dimension in correction_by_dimension:
            correction = correction_by_dimension[dimension]
            item = ProfileGuidanceItem(
                dimension=ProfileDimension(dimension),
                guidance=correction.correction_text.strip(),
                confidence=1.0,
                explicit=True,
            )
            rank = (4, 1.0, correction.created_at or now)
            winners[dimension] = item
            winner_rank[dimension] = rank
            continue

        rank = _rank_signal(signal, corrected_dimensions=corrected_dims)
        current = winner_rank.get(dimension)
        if current is None or rank > current:
            winners[dimension] = ProfileGuidanceItem(
                dimension=ProfileDimension(dimension),
                guidance=signal.content,
                confidence=float(signal.confidence or 0.0),
                explicit=(signal.explicitness == "explicit"),
            )
            winner_rank[dimension] = rank

    items = tuple(
        winners[key]
        for key in sorted(winners.keys(), key=lambda d: winners[d].dimension.value)
    )
    return ProfileGuidance(
        subject_id=user_id,
        team_id=team_id,
        items=items,
        compiled_at=now,
    )


def persist_effective_profile(
    session: Session,
    guidance: ProfileGuidance,
) -> ProfileEffectiveRow:
    row = session.scalar(
        select(ProfileEffectiveRow).where(
            ProfileEffectiveRow.user_id == guidance.subject_id,
            ProfileEffectiveRow.team_id == guidance.team_id,
        )
    )
    now = guidance.compiled_at or datetime.now(timezone.utc)
    snapshot = guidance.model_dump(mode="json")
    if row is None:
        row = ProfileEffectiveRow(
            user_id=guidance.subject_id,
            team_id=guidance.team_id,
            snapshot_json=snapshot,
            compiled_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.snapshot_json = snapshot
        row.compiled_at = now
        row.updated_at = now
    session.flush()
    return row


def compile_and_persist_effective_profile(
    session: Session,
    *,
    user_id: str,
    team_id: str,
) -> ProfileGuidance:
    guidance = compile_profile_guidance(session, user_id=user_id, team_id=team_id)
    persist_effective_profile(session, guidance)
    return guidance
