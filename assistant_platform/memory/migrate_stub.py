"""Optional migration helper: ensure semantic memory tables exist on assistant DB.

Copies any legacy ``pm_memory_atoms``/``pm_commitments`` rows (from a shared
Pulse database) into the ``ap_semantic_atoms``/``ap_commitments`` tables. This
is idempotent and safe to re-run; it is a no-op when the legacy ``pm_*``
tables don't exist.
"""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine

from assistant_platform.memory.semantic.migrate import migrate_pm_to_semantic
from assistant_platform.memory.semantic.models import (
    CommitmentRow,
    DisclosureLogRow,
    SemanticAtomRow,
)
from assistant_platform.storage.models import Base


def migrate(database_url: str) -> int:
    engine = create_engine(database_url)
    Base.metadata.create_all(
        engine,
        tables=[
            SemanticAtomRow.__table__,
            CommitmentRow.__table__,
            DisclosureLogRow.__table__,
        ],
    )
    summary = migrate_pm_to_semantic(engine)
    print(
        "semantic memory tables ready; "
        f"copied atoms={summary['atoms']} commitments={summary['commitments']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure semantic memory tables on assistant DB")
    parser.add_argument(
        "--database-url",
        default="sqlite:///data/assistant.db",
        help="SQLAlchemy database URL (default: sqlite:///data/assistant.db)",
    )
    args = parser.parse_args()
    return migrate(args.database_url)


if __name__ == "__main__":
    raise SystemExit(main())
