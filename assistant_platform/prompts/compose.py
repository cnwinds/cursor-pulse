from __future__ import annotations

from typing import Any

from assistant_platform.prompts.loader import (
    PERSONA_SUPPLEMENT_HEADER,
    compose_system_supplement_from_files,
    load_prompt_fragments_from_files,
)

# re-export for tests/imports
__all__ = [
    "PERSONA_SUPPLEMENT_HEADER",
    "compose_system_supplement",
    "load_prompt_fragments",
]


def load_prompt_fragments(db_session: Any = None, release_id: str | None = None) -> dict[str, str]:
    return load_prompt_fragments_from_files()


def compose_system_supplement(db_session: Any = None, release_id: str | None = None) -> str:
    return compose_system_supplement_from_files()
