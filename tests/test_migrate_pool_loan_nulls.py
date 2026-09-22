"""Existing SQLite key_loans can drop NOT NULL on source account and credential."""

from sqlalchemy import create_engine, inspect, text

from pulse.storage.migrate import _relax_key_loan_account_nulls


def test_sqlite_rebuild_allows_null_source_account():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE key_loans (
                    id VARCHAR(36) NOT NULL,
                    source_account_id VARCHAR(36) NOT NULL,
                    credential_id VARCHAR(36) NOT NULL,
                    routing_mode VARCHAR(16) DEFAULT 'pinned',
                    PRIMARY KEY (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO key_loans (id, source_account_id, credential_id) "
                "VALUES ('keep', 'acct', 'cred')"
            )
        )
    _relax_key_loan_account_nulls(engine)
    cols = {col["name"]: col for col in inspect(engine).get_columns("key_loans")}
    assert cols["source_account_id"]["nullable"] is True
    assert cols["credential_id"]["nullable"] is True
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO key_loans (id, source_account_id, credential_id, routing_mode) "
                "VALUES ('pool', NULL, NULL, 'pool')"
            )
        )
        kept = conn.execute(text("SELECT source_account_id FROM key_loans WHERE id = 'keep'")).scalar()
    assert kept == "acct"
