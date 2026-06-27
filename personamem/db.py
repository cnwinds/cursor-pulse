from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from personamem.models import Base


def migrate_memory_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    Base.metadata.create_all(engine)

    if "pm_memory_atoms" in tables:
        columns = {col["name"] for col in inspector.get_columns("pm_memory_atoms")}
        if "embedding_json" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE pm_memory_atoms ADD COLUMN embedding_json JSON"))


def init_memory_tables(engine: Engine) -> None:
    migrate_memory_schema(engine)
