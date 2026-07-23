from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.prompts.fragments import (
    AGENT_TOOLS_RELEASE_NAME,
    PERSONA_ONLY_RELEASE_NAME,
    canonical_fragments,
    is_business_rule_precepts_content,
    is_legacy_precepts_content,
)
from assistant_platform.prompts.models import (
    PromptFragmentRow,
    PromptReleaseRow,
)

logger = logging.getLogger(__name__)

DEFAULT_RELEASE_NAME = "v0-default"

# Backward-compatible alias for tests importing _FRAGMENT_STUBS.
_FRAGMENT_STUBS = canonical_fragments()


def get_production_release(session: Session) -> PromptReleaseRow | None:
    return session.scalar(
        select(PromptReleaseRow).where(PromptReleaseRow.status == "production")
    )


def _fragment_texts_for_release(session: Session, release_id: str) -> dict[str, str]:
    release = session.get(PromptReleaseRow, release_id)
    if release is None:
        return {}
    out: dict[str, str] = {}
    for fragment_id in release.fragment_ids_json or []:
        row = session.get(PromptFragmentRow, fragment_id)
        if row and row.content:
            out[row.key] = row.content
    return out


def production_needs_agent_tools_upgrade(session: Session) -> bool:
    """True when production still carries legacy pre-agent precepts wording."""
    production = get_production_release(session)
    if production is None:
        return False
    if production.name in (AGENT_TOOLS_RELEASE_NAME, PERSONA_ONLY_RELEASE_NAME):
        return False
    fragments = _fragment_texts_for_release(session, production.id)
    return is_legacy_precepts_content(fragments.get("precepts.md", ""))


def production_needs_persona_only_upgrade(session: Session) -> bool:
    """True when production still uses v2-era business-rule precepts or v2 release name."""
    production = get_production_release(session)
    if production is None:
        return False
    if production.name == PERSONA_ONLY_RELEASE_NAME:
        return False
    fragments = _fragment_texts_for_release(session, production.id)
    precepts = fragments.get("precepts.md", "")
    if production.name == AGENT_TOOLS_RELEASE_NAME:
        return True
    return is_business_rule_precepts_content(precepts)


def seed_default_prompt_release(session: Session) -> PromptReleaseRow | None:
    """Return existing production release if present; otherwise no-op (files are source of truth)."""
    return get_production_release(session)


def upgrade_production_to_agent_tools(session: Session) -> PromptReleaseRow | None:
    """No-op: prompt releases are no longer auto-upgraded at startup."""
    return get_production_release(session)


def upgrade_production_to_persona_only(session: Session) -> PromptReleaseRow | None:
    """No-op: prompt releases are no longer auto-upgraded at startup."""
    return get_production_release(session)


def _production_differs_from_files(session: Session, production: PromptReleaseRow) -> bool:
    fragments = _fragment_texts_for_release(session, production.id)
    canonical = {stub["key"]: stub["content"] for stub in canonical_fragments()}
    return fragments != canonical


def ensure_production_prompt_release(session: Session) -> PromptReleaseRow | None:
    """Preserve existing DB production rows; do not seed or upgrade on startup."""
    production = get_production_release(session)
    if production is None:
        return seed_default_prompt_release(session)
    if _production_differs_from_files(session, production):
        logger.warning(
            "ap_prompt production differs from files; files are source of truth"
        )
    return production
