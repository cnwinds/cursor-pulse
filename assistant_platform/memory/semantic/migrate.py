"""One-time, idempotent copy of legacy ``pm_*`` (personamem) rows into ``ap_*``.

Reads the old tables with raw SQL only (no ``personamem`` import) so this can
run even after the ``personamem`` package is removed, as long as the physical
``pm_memory_atoms`` / ``pm_commitments`` tables still exist in the target
database. Safe to run multiple times: existing ``ap_*`` rows (matched by
primary key) are left untouched.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_ATOM_COLUMNS = (
    "id",
    "namespace",
    "subject_id",
    "kind",
    "content",
    "source_visibility",
    "sensitivity",
    "confidence",
    "created_at",
    "last_seen_at",
    "first_confirmed_at",
    "supersedes_id",
    "status",
    "evidence_json",
    "embedding_json",
)

_COMMITMENT_COLUMNS = (
    "id",
    "namespace",
    "counterparty_id",
    "type",
    "statement",
    "scope",
    "status",
    "first_confirmed_at",
    "last_confirmed_at",
    "evidence_json",
    "supersedes_id",
    "created_at",
)

# Legacy ``pm_*`` rows often have NULL JSON columns; ``ap_*`` tables require
# NOT NULL values (ORM defaults do not apply to raw SQL inserts). Use JSON
# strings because sqlite3 parameter binding does not accept dict values.
_JSON_COLUMN_DEFAULTS: dict[str, str] = {
    "evidence_json": "{}",
    "scope": "{}",
}


def _normalize_legacy_row(row: dict, columns: tuple[str, ...]) -> dict:
    payload = dict(row)
    for col in columns:
        if col not in _JSON_COLUMN_DEFAULTS:
            continue
        val = payload.get(col)
        if val is None:
            payload[col] = _JSON_COLUMN_DEFAULTS[col]
        elif isinstance(val, (dict, list)):
            payload[col] = json.dumps(val, ensure_ascii=False)
    return payload


def _copy_table(engine: Engine, *, source: str, target: str, columns: tuple[str, ...]) -> int:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if source not in tables or target not in tables:
        return 0

    col_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    copied = 0
    with engine.begin() as conn:
        existing_ids = {
            row[0] for row in conn.execute(text(f"SELECT id FROM {target}")).fetchall()
        }
        rows = conn.execute(text(f"SELECT {col_list} FROM {source}")).mappings().all()
        for row in rows:
            if row["id"] in existing_ids:
                continue
            conn.execute(
                text(f"INSERT INTO {target} ({col_list}) VALUES ({placeholders})"),
                _normalize_legacy_row(row, columns),
            )
            existing_ids.add(row["id"])
            copied += 1
    return copied


def migrate_pm_to_semantic(engine: Engine) -> dict[str, int]:
    """Copy any not-yet-migrated ``pm_memory_atoms``/``pm_commitments`` rows.

    Returns a summary dict of counts copied per table. No-ops (returns zeros)
    when the legacy ``pm_*`` tables don't exist in this database.
    """
    atoms_copied = _copy_table(
        engine,
        source="pm_memory_atoms",
        target="ap_semantic_atoms",
        columns=_ATOM_COLUMNS,
    )
    commitments_copied = _copy_table(
        engine,
        source="pm_commitments",
        target="ap_commitments",
        columns=_COMMITMENT_COLUMNS,
    )
    if atoms_copied or commitments_copied:
        logger.info(
            "personamem migration: copied %s atoms, %s commitments into ap_semantic_* tables",
            atoms_copied,
            commitments_copied,
        )
    return {"atoms": atoms_copied, "commitments": commitments_copied}
