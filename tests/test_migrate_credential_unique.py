from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from pulse.storage.migrate import migrate_schema
from pulse.storage.models import AiAccountCredential, Base


def _legacy_credentials_sql() -> str:
    return """
    CREATE TABLE ai_account_credentials (
        id VARCHAR(36) PRIMARY KEY,
        account_id VARCHAR(36) NOT NULL,
        vendor_id VARCHAR(36) NOT NULL,
        credential_type VARCHAR(32) NOT NULL,
        encrypted_value TEXT NOT NULL,
        key_hint VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL,
        bound_by_member_id VARCHAR(36) NOT NULL,
        bound_at DATETIME NOT NULL,
        last_validated_at DATETIME,
        last_sync_at DATETIME,
        last_sync_status VARCHAR(16) NOT NULL,
        last_sync_error TEXT,
        sync_enabled BOOLEAN NOT NULL,
        CONSTRAINT uq_credential_account UNIQUE (account_id)
    )
    """


def test_migrate_drops_uq_credential_account():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE ai_account_credentials"))
        conn.execute(text(_legacy_credentials_sql()))

    migrate_schema(engine)

    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("ai_account_credentials")
    assert not any(uc.get("name") == "uq_credential_account" for uc in unique_constraints)

    session = sessionmaker(bind=engine)()
    session.add(
        AiAccountCredential(
            id="cred-1",
            account_id="acct-1",
            vendor_id="vendor-1",
            credential_type="cursor_api_key",
            encrypted_value="enc-1",
            key_hint="sk-ab...hint",
            key_role="primary",
            bound_by_member_id="member-1",
            status="active",
            last_sync_status="never",
            sync_enabled=True,
        )
    )
    session.add(
        AiAccountCredential(
            id="cred-2",
            account_id="acct-1",
            vendor_id="vendor-1",
            credential_type="cursor_api_key",
            encrypted_value="enc-2",
            key_hint="sk-cd...hint",
            key_role="loan",
            bound_by_member_id="member-1",
            status="active",
            last_sync_status="never",
            sync_enabled=False,
        )
    )
    session.commit()
    session.close()
