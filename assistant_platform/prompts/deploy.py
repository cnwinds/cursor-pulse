from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.prompts.models import PromptDeploymentRow, PromptReleaseRow
from assistant_platform.prompts.seed import get_production_release

# Mandatory production path — NEVER skip steps:
#   proposal (draft) -> human approve -> deploy_canary -> promote_release
# Proposals and draft releases must NOT reach production without this chain.


def session_in_canary_bucket(session_id: str, *, percent: int) -> bool:
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    digest = int(hashlib.sha256(session_id.encode()).hexdigest(), 16)
    return (digest % 100) < percent


def _active_canary_deployment(db_session: Session) -> PromptDeploymentRow | None:
    return db_session.scalar(
        select(PromptDeploymentRow)
        .join(PromptReleaseRow, PromptReleaseRow.id == PromptDeploymentRow.release_id)
        .where(
            PromptReleaseRow.status == "canary",
            PromptDeploymentRow.status == "active",
            PromptDeploymentRow.percent > 0,
        )
        .order_by(PromptDeploymentRow.id.desc())
    )


def resolve_prompt_release_for_new_session(
    db_session: Session,
    session_id: str,
) -> PromptReleaseRow | None:
    """Deprecated: new sessions no longer pin prompt_release_id; compose uses files."""
    production = get_production_release(db_session)
    deployment = _active_canary_deployment(db_session)
    if deployment is not None:
        canary = db_session.get(PromptReleaseRow, deployment.release_id)
        if canary is not None and session_in_canary_bucket(
            session_id, percent=deployment.percent
        ):
            return canary
    return production


def deploy_canary(
    db_session: Session,
    release_id: str,
    *,
    percent: int = 10,
) -> PromptReleaseRow:
    release = db_session.get(PromptReleaseRow, release_id)
    if release is None:
        raise ValueError(f"release not found: {release_id}")
    if release.status == "production":
        raise ValueError("cannot canary a production release")

    for other in db_session.scalars(
        select(PromptDeploymentRow).join(
            PromptReleaseRow, PromptReleaseRow.id == PromptDeploymentRow.release_id
        ).where(
            PromptReleaseRow.status == "canary",
            PromptDeploymentRow.status == "active",
        )
    ):
        other.status = "retired"
        db_session.add(other)

    release.status = "canary"
    db_session.add(release)
    db_session.add(
        PromptDeploymentRow(
            release_id=release.id,
            percent=max(0, min(100, percent)),
            status="active",
        )
    )
    db_session.flush()
    return release


def promote_release(db_session: Session, release_id: str) -> PromptReleaseRow:
    release = db_session.get(PromptReleaseRow, release_id)
    if release is None:
        raise ValueError(f"release not found: {release_id}")

    current = get_production_release(db_session)
    if current is not None and current.id != release.id:
        current.status = "retired"
        db_session.add(current)
        for dep in db_session.scalars(
            select(PromptDeploymentRow).where(
                PromptDeploymentRow.release_id == current.id,
                PromptDeploymentRow.status == "active",
            )
        ):
            dep.status = "retired"
            db_session.add(dep)

    if release.status == "canary":
        for dep in db_session.scalars(
            select(PromptDeploymentRow).where(
                PromptDeploymentRow.release_id == release.id,
                PromptDeploymentRow.status == "active",
            )
        ):
            dep.status = "retired"
            db_session.add(dep)

    release.status = "production"
    db_session.add(release)
    db_session.add(
        PromptDeploymentRow(
            release_id=release.id,
            percent=100,
            status="active",
        )
    )
    db_session.flush()
    return release


def rollback_production(db_session: Session, to_release_id: str) -> PromptReleaseRow:
    target = db_session.get(PromptReleaseRow, to_release_id)
    if target is None:
        raise ValueError(f"release not found: {to_release_id}")

    current = get_production_release(db_session)
    if current is not None and current.id != target.id:
        current.status = "retired"
        db_session.add(current)
        for dep in db_session.scalars(
            select(PromptDeploymentRow).where(
                PromptDeploymentRow.release_id == current.id,
                PromptDeploymentRow.status == "active",
            )
        ):
            dep.status = "retired"
            db_session.add(dep)

    target.status = "production"
    db_session.add(target)
    for dep in db_session.scalars(
        select(PromptDeploymentRow).where(
            PromptDeploymentRow.release_id == target.id,
            PromptDeploymentRow.status == "active",
        )
    ):
        dep.status = "retired"
        db_session.add(dep)
    db_session.add(
        PromptDeploymentRow(
            release_id=target.id,
            percent=100,
            status="active",
        )
    )
    db_session.flush()
    return target
