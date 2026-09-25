"""migrate_schema must not rewrite pkcp_ keys to mode=quota."""

from pulse.storage.migrate import migrate_schema
from sqlalchemy import create_engine, text


def test_migrate_preserves_coding_plan_proxy_key_mode():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE proxy_keys (
                    id VARCHAR(36) PRIMARY KEY,
                    key_hash VARCHAR(64) NOT NULL,
                    key_hint VARCHAR(16) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    member_id VARCHAR(36) NOT NULL,
                    mode VARCHAR(16),
                    coding_plan_vendor VARCHAR(16),
                    status VARCHAR(16) DEFAULT 'active'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO proxy_keys (id, key_hash, key_hint, name, member_id, mode, coding_plan_vendor)
                VALUES ('cp1', 'hash', 'pkcp_x', 'GLM OpenAI', 'm1', 'coding_plan', 'glm')
                """
            )
        )
    migrate_schema(engine)
    with engine.begin() as conn:
        mode = conn.execute(text("SELECT mode FROM proxy_keys WHERE id = 'cp1'")).scalar()
        vendor = conn.execute(text("SELECT coding_plan_vendor FROM proxy_keys WHERE id = 'cp1'")).scalar()
    assert mode == "coding_plan"
    assert vendor == "glm"
