from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from sqlalchemy import text

from pulse.storage.migrate import migrate_schema
from pulse.storage.models import Base, ProxyKeyUsage


def test_proxy_key_usage_allows_loan_id_without_proxy_key(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    migrate_schema(engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("proxy_key_usages")}
    assert "loan_id" in cols
    assert cols["proxy_key_id"]["nullable"] is True
    cred_cols = {c["name"] for c in inspect(engine).get_columns("ai_account_credentials")}
    assert "key_hash" in cred_cols

    with Session(engine) as s:
        s.add(
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id="loan-1",
                credential_id="cred-1",
                total_tokens=10,
                cost_cents=3,
            )
        )
        s.commit()


def test_key_loans_alias_columns_and_unique_index_migrated(tmp_path):
    """存量表无别名字段时，migrate 应补齐列与唯一索引。"""
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE key_loans (
                    id VARCHAR(36) PRIMARY KEY,
                    source_account_id VARCHAR(36),
                    credential_id VARCHAR(36),
                    borrower_member_id VARCHAR(36),
                    borrower_note TEXT,
                    baseline_used_cents INTEGER,
                    created_at DATETIME,
                    revoked_at DATETIME,
                    status VARCHAR(16),
                    auto_revoke_on_reset BOOLEAN,
                    note TEXT
                )
                """
            )
        )
    migrate_schema(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("key_loans")}
    assert "delivery_mode" in cols
    assert "alias_key_hash" in cols
    assert "alias_key_hint" in cols
    assert "alias_encrypted_key" in cols
    indexes = {idx["name"]: idx for idx in inspect(engine).get_indexes("key_loans")}
    assert "ix_key_loans_alias_key_hash" in indexes
    assert bool(indexes["ix_key_loans_alias_key_hash"].get("unique"))
