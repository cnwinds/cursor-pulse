"""存量库补齐 Auto Lender 列：key_loans.lender_mode / source_bound_at、ai_accounts.proxy_reserve_pct。"""

from __future__ import annotations

from pulse.storage.migrate import migrate_schema
from pulse.storage.models import Base
from sqlalchemy import create_engine, inspect, text


def _drop_auto_lender_columns(engine) -> None:
    """把新列从库里去掉，模拟升级前的存量库。"""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE key_loans"))
        conn.execute(text("DROP TABLE ai_accounts"))
        conn.execute(
            text(
                """
                CREATE TABLE ai_accounts (
                    id VARCHAR(36) PRIMARY KEY,
                    team_id VARCHAR(36),
                    vendor_id VARCHAR(36) NOT NULL,
                    plan_id VARCHAR(36) NOT NULL,
                    account_identifier VARCHAR(256) NOT NULL,
                    ownership VARCHAR(16) NOT NULL DEFAULT 'company',
                    status VARCHAR(16) NOT NULL DEFAULT 'shared',
                    primary_member_id VARCHAR(36),
                    proxy_enabled BOOLEAN DEFAULT 0,
                    proxy_score_adjust FLOAT,
                    deleted_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE key_loans (
                    id VARCHAR(36) PRIMARY KEY,
                    source_account_id VARCHAR(36) NOT NULL,
                    credential_id VARCHAR(36) NOT NULL,
                    borrower_member_id VARCHAR(36),
                    borrower_note TEXT,
                    baseline_used_cents INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    revoked_at DATETIME,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    auto_revoke_on_reset BOOLEAN NOT NULL DEFAULT 1,
                    expires_on DATE,
                    note TEXT,
                    delivery_mode VARCHAR(32) DEFAULT 'cursor_direct',
                    alias_key_hash VARCHAR(64),
                    alias_key_hint VARCHAR(32),
                    alias_encrypted_key TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO key_loans (id, source_account_id, credential_id, "
                "created_at, status) VALUES ('loan-1', 'acct-1', 'cred-1', "
                "'2026-07-01T00:00:00', 'active')"
            )
        )


def test_migrate_adds_auto_lender_columns_and_backfills_bound_at():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _drop_auto_lender_columns(engine)

    migrate_schema(engine)

    loan_cols = {c["name"] for c in inspect(engine).get_columns("key_loans")}
    assert {"lender_mode", "source_bound_at"} <= loan_cols
    account_cols = {c["name"] for c in inspect(engine).get_columns("ai_accounts")}
    assert "proxy_reserve_pct" in account_cols

    with engine.begin() as conn:
        row = conn.execute(text("SELECT lender_mode, source_bound_at FROM key_loans WHERE id = 'loan-1'")).one()
    # 存量借用视为人工固定关系，绑定时刻回填为创建时刻
    assert row[0] == "manual"
    assert row[1] is not None
    assert str(row[1]).startswith("2026-07-01")


def test_migrate_auto_lender_columns_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    migrate_schema(engine)
    migrate_schema(engine)

    loan_cols = {c["name"] for c in inspect(engine).get_columns("key_loans")}
    assert {"lender_mode", "source_bound_at"} <= loan_cols
