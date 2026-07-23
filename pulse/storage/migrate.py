from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from pulse.storage.models import Base, Team

logger = logging.getLogger(__name__)

_TEAM_ID_TABLES = ("members", "metric_snapshots", "reports", "alert_logs")

_MEMBER_PORTAL_COLUMNS: dict[str, str] = {
    "portal_status": "VARCHAR(16)",
    "portal_role": "VARCHAR(16)",
    "portal_permissions": "JSON",
    "password_hash": "VARCHAR(256)",
    "last_portal_login_at": "DATETIME",
}

_CREDENTIAL_PROXY_COLUMNS: dict[str, str] = {
    "proxy_enabled": "BOOLEAN DEFAULT 0",
    "key_hash": "VARCHAR(64)",
}

_ACCOUNT_PROXY_COLUMNS: dict[str, str] = {
    "proxy_enabled": "BOOLEAN DEFAULT 0",
}

_PROXY_USAGE_COLUMNS: dict[str, str] = {
    "request_id": "VARCHAR(64)",
    "loan_id": "VARCHAR(36)",
}
_PROXY_KEY_COLUMNS: dict[str, str] = {"encrypted_key": "TEXT"}
_PROXY_EVENT_COLUMNS: dict[str, str] = {"loan_id": "VARCHAR(36)"}

_KEY_LOAN_ALIAS_COLUMNS: dict[str, str] = {
    "delivery_mode": "VARCHAR(32) DEFAULT 'cursor_direct'",
    "alias_key_hash": "VARCHAR(64)",
    "alias_key_hint": "VARCHAR(32)",
    "alias_encrypted_key": "TEXT",
}


_MEMBER_V2_COLUMNS: dict[str, str] = {
    "department_name": "VARCHAR(128)",
    "manager_dingtalk_user_id": "VARCHAR(64)",
    "manager_member_id": "VARCHAR(36)",
    "employment_status": "VARCHAR(16) DEFAULT 'active'",
}

_SUBMISSION_V2_COLUMNS: dict[str, str] = {
    "account_id": "VARCHAR(36)",
    "vendor_id": "VARCHAR(36)",
}

_AI_ACCOUNT_V2_COLUMNS: dict[str, str] = {
    "usage_resets_on": "DATE",
    "resets_on_source": "VARCHAR(16) DEFAULT 'manual'",
    "deleted_at": "DATETIME",
}

_AI_CREDENTIAL_COLUMNS: dict[str, str] = {
    "key_role": "VARCHAR(16) DEFAULT 'primary'",
    "display_name": "VARCHAR(128)",
    "remote_key_id": "INTEGER",
    "assignee_member_id": "VARCHAR(36)",
}

_AI_CREDENTIAL_SYNC_SCHEDULE_COLUMNS: dict[str, str] = {
    "next_sync_at": "DATETIME",
    "next_retry_at": "DATETIME",
    "sync_priority": "VARCHAR(16) DEFAULT 'normal'",
    "retry_count": "INTEGER DEFAULT 0",
    "sync_jitter_sec": "INTEGER DEFAULT 0",
}

_USAGE_RECORD_PRICING_COLUMNS: dict[str, str] = {
    "cost_estimated_usd": "NUMERIC(12, 6) DEFAULT 0",
    "cost_basis": "VARCHAR(16) DEFAULT 'none'",
    "pricing_version": "VARCHAR(32)",
    "pricing_rule": "VARCHAR(128)",
}

_USAGE_SUMMARY_PRICING_COLUMNS: dict[str, str] = {
    "reported_spend_usd": "NUMERIC(12, 4)",
    "estimated_included_spend_usd": "NUMERIC(12, 4)",
    "estimation_coverage_pct": "FLOAT",
    "unmatched_models": "JSON",
}

_USAGE_SUMMARY_CYCLE_COLUMNS: dict[str, str] = {
    "billing_cycle_start": "DATE",
    "billing_cycle_end": "DATE",
    "plan_id_used": "VARCHAR(36)",
    "quota_denominator_snapshot": "NUMERIC(12, 4)",
    "cycle_metric_value": "NUMERIC(12, 4)",
    "cycle_quota_usage_ratio": "FLOAT",
}

_USAGE_SUMMARY_CURSOR_POOLS_COLUMNS: dict[str, str] = {
    "cursor_pools": "JSON",
    "external_models": "JSON",
}

_USAGE_RECORD_INGESTION_COLUMNS: dict[str, str] = {
    "ingestion_id": "VARCHAR(36)",
    "external_id": "VARCHAR(64)",
}

_USAGE_SUMMARY_INGESTION_COLUMNS: dict[str, str] = {
    "latest_ingestion_id": "VARCHAR(36)",
    "sync_source": "VARCHAR(16)",
    "last_synced_at": "DATETIME",
}


def _sqlite_drop_column(engine: Engine, table_name: str, column_name: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            try:
                conn.execute(text(f"ALTER TABLE {table_name} DROP COLUMN {column_name}"))
                logger.info("Dropped %s from %s", column_name, table_name)
                return
            except Exception:
                logger.info("DROP COLUMN failed for %s.%s; rebuilding table", table_name, column_name)

            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            keep_rows = [row for row in rows if row[1] != column_name]
            if len(keep_rows) == len(rows):
                return

            col_defs: list[str] = []
            col_names: list[str] = []
            for _cid, name, col_type, notnull, default, pk in keep_rows:
                col_names.append(name)
                definition = f'"{name}" {col_type}'
                if pk:
                    definition += " PRIMARY KEY"
                elif notnull:
                    definition += " NOT NULL"
                if default is not None:
                    definition += f" DEFAULT {default}"
                col_defs.append(definition)

            quoted_cols = ", ".join(f'"{name}"' for name in col_names)
            conn.execute(
                text(f'CREATE TABLE {table_name}__new ({", ".join(col_defs)})')
            )
            conn.execute(
                text(
                    f"INSERT INTO {table_name}__new ({quoted_cols}) "
                    f"SELECT {quoted_cols} FROM {table_name}"
                )
            )
            conn.execute(text(f"DROP TABLE {table_name}"))
            conn.execute(text(f"ALTER TABLE {table_name}__new RENAME TO {table_name}"))
            logger.info("Rebuilt %s without %s", table_name, column_name)
        finally:
            conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_legacy_submission_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "usage_records" in tables:
        columns = {col["name"] for col in inspector.get_columns("usage_records")}
        if "submission_id" in columns:
            with engine.begin() as conn:
                if "ingestion_id" in columns:
                    conn.execute(
                        text(
                            "UPDATE usage_records "
                            "SET ingestion_id = submission_id "
                            "WHERE ingestion_id IS NULL AND submission_id IS NOT NULL"
                        )
                    )
            _sqlite_drop_column(engine, "usage_records", "submission_id")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "usage_summaries" in tables:
        columns = {col["name"] for col in inspector.get_columns("usage_summaries")}
        if "submission_id" in columns:
            with engine.begin() as conn:
                if "latest_ingestion_id" in columns:
                    conn.execute(
                        text(
                            "UPDATE usage_summaries "
                            "SET latest_ingestion_id = submission_id "
                            "WHERE latest_ingestion_id IS NULL AND submission_id IS NOT NULL"
                        )
                    )
            _sqlite_drop_column(engine, "usage_summaries", "submission_id")


def _rebuild_submission_to_ingestion(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "submissions" in tables and "usage_ingestions" not in tables:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS usage_records"))
            conn.execute(text("DROP TABLE IF EXISTS submissions"))
        logger.warning("Dropped legacy submissions/usage_records for ingestion migration")


def _sqlite_rebuild_without_unique_account(engine: Engine) -> None:
    """Remove uq_credential_account so multiple keys per account are allowed."""
    inspector = inspect(engine)
    if "ai_account_credentials" not in inspector.get_table_names():
        return
    unique_constraints = inspector.get_unique_constraints("ai_account_credentials")
    has_uq = any(
        uc.get("name") == "uq_credential_account" for uc in unique_constraints
    )
    if not has_uq:
        return

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            rows = conn.execute(text("PRAGMA table_info(ai_account_credentials)")).fetchall()
            col_defs: list[str] = []
            col_names: list[str] = []
            for _cid, name, col_type, notnull, default, pk in rows:
                col_names.append(name)
                definition = f'"{name}" {col_type}'
                if pk:
                    definition += " PRIMARY KEY"
                elif notnull:
                    definition += " NOT NULL"
                if default is not None:
                    definition += f" DEFAULT {default}"
                col_defs.append(definition)

            quoted_cols = ", ".join(f'"{name}"' for name in col_names)
            conn.execute(
                text(f'CREATE TABLE ai_account_credentials__new ({", ".join(col_defs)})')
            )
            conn.execute(
                text(
                    f"INSERT INTO ai_account_credentials__new ({quoted_cols}) "
                    f"SELECT {quoted_cols} FROM ai_account_credentials"
                )
            )
            conn.execute(text("DROP TABLE ai_account_credentials"))
            conn.execute(
                text("ALTER TABLE ai_account_credentials__new RENAME TO ai_account_credentials")
            )
            logger.info("Rebuilt ai_account_credentials without uq_credential_account")
        finally:
            conn.execute(text("PRAGMA foreign_keys=ON"))


def _sqlite_rebuild_proxy_key_usages_nullable_proxy_key(engine: Engine) -> None:
    """Make proxy_key_id nullable on proxy_key_usages (SQLite cannot ALTER COLUMN)."""
    inspector = inspect(engine)
    if "proxy_key_usages" not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns("proxy_key_usages")}
    if "proxy_key_id" not in cols or cols["proxy_key_id"]["nullable"]:
        return

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            rows = conn.execute(text("PRAGMA table_info(proxy_key_usages)")).fetchall()
            col_defs: list[str] = []
            col_names: list[str] = []
            for _cid, name, col_type, notnull, default, pk in rows:
                col_names.append(name)
                definition = f'"{name}" {col_type}'
                if pk:
                    definition += " PRIMARY KEY"
                elif notnull and name != "proxy_key_id":
                    definition += " NOT NULL"
                if default is not None:
                    definition += f" DEFAULT {default}"
                col_defs.append(definition)

            quoted_cols = ", ".join(f'"{name}"' for name in col_names)
            conn.execute(
                text(f'CREATE TABLE proxy_key_usages__new ({", ".join(col_defs)})')
            )
            conn.execute(
                text(
                    f"INSERT INTO proxy_key_usages__new ({quoted_cols}) "
                    f"SELECT {quoted_cols} FROM proxy_key_usages"
                )
            )
            conn.execute(text("DROP TABLE proxy_key_usages"))
            conn.execute(
                text("ALTER TABLE proxy_key_usages__new RENAME TO proxy_key_usages")
            )
            logger.info("Rebuilt proxy_key_usages with nullable proxy_key_id")
        finally:
            conn.execute(text("PRAGMA foreign_keys=ON"))


def migrate_schema(engine: Engine) -> None:
    """轻量迁移：为已有 SQLite 库补齐表与列。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "teams" not in tables:
        Team.__table__.create(engine)
        logger.info("Created teams table")

    for table in _TEAM_ID_TABLES:
        if table not in tables:
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        if "team_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN team_id VARCHAR(36)"))
            logger.info("Added team_id column to %s", table)

    if "members" in tables:
        columns = {col["name"] for col in inspector.get_columns("members")}
        for col_name, col_type in _MEMBER_PORTAL_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE members ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to members", col_name)

        columns = {col["name"] for col in inspector.get_columns("members")}
        for col_name, col_type in _MEMBER_V2_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE members ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to members", col_name)

        columns = {col["name"] for col in inspector.get_columns("members")}
        if "portal_status" in columns:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE members SET portal_status = 'active' "
                        "WHERE portal_role IS NOT NULL AND portal_status IS NULL"
                    )
                )

    if "submissions" in tables:
        columns = {col["name"] for col in inspector.get_columns("submissions")}
        for col_name, col_type in _SUBMISSION_V2_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE submissions ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to submissions", col_name)

    if "ai_accounts" in tables:
        columns = {col["name"] for col in inspector.get_columns("ai_accounts")}
        for col_name, col_type in _AI_ACCOUNT_V2_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE ai_accounts ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to ai_accounts", col_name)
        columns = {col["name"] for col in inspector.get_columns("ai_accounts")}
        if "resets_on_source" in columns and "usage_resets_on" in columns:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE ai_accounts SET resets_on_source = 'manual-locked' "
                        "WHERE usage_resets_on IS NOT NULL "
                        "AND (resets_on_source IS NULL OR resets_on_source = 'manual')"
                    )
                )

    if "ai_account_credentials" in tables:
        columns = {col["name"] for col in inspector.get_columns("ai_account_credentials")}
        for col_name, col_type in _AI_CREDENTIAL_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE ai_account_credentials ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to ai_account_credentials", col_name)
        columns = {col["name"] for col in inspector.get_columns("ai_account_credentials")}
        if "key_role" in columns:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE ai_account_credentials SET key_role = 'primary' "
                        "WHERE key_role IS NULL OR key_role = ''"
                    )
                )
        _sqlite_rebuild_without_unique_account(engine)
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("ai_account_credentials")}
        for col_name, col_type in _AI_CREDENTIAL_SYNC_SCHEDULE_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE ai_account_credentials ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to ai_account_credentials", col_name)

    if "usage_records" in tables:
        columns = {col["name"] for col in inspector.get_columns("usage_records")}
        for col_name, col_type in _USAGE_RECORD_PRICING_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE usage_records ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to usage_records", col_name)

    if "usage_summaries" in tables:
        columns = {col["name"] for col in inspector.get_columns("usage_summaries")}
        for col_name, col_type in _USAGE_SUMMARY_PRICING_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE usage_summaries ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to usage_summaries", col_name)
        columns = {col["name"] for col in inspector.get_columns("usage_summaries")}
        for col_name, col_type in _USAGE_SUMMARY_CYCLE_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE usage_summaries ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to usage_summaries", col_name)
        columns = {col["name"] for col in inspector.get_columns("usage_summaries")}
        for col_name, col_type in _USAGE_SUMMARY_CURSOR_POOLS_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE usage_summaries ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to usage_summaries", col_name)

    _rebuild_submission_to_ingestion(engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "usage_records" in tables:
        columns = {col["name"] for col in inspector.get_columns("usage_records")}
        if "submission_id" in columns:
            for col_name, col_type in _USAGE_RECORD_INGESTION_COLUMNS.items():
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE usage_records ADD COLUMN {col_name} {col_type}")
                        )
                    logger.info("Added %s column to usage_records", col_name)

    if "usage_summaries" in tables:
        columns = {col["name"] for col in inspector.get_columns("usage_summaries")}
        for col_name, col_type in _USAGE_SUMMARY_INGESTION_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE usage_summaries ADD COLUMN {col_name} {col_type}"))
                logger.info("Added %s column to usage_summaries", col_name)

    _migrate_legacy_submission_columns(engine)

    if "ai_account_credentials" in tables:
        columns = {col["name"] for col in inspector.get_columns("ai_account_credentials")}
        for col_name, col_type in _CREDENTIAL_PROXY_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE ai_account_credentials ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to ai_account_credentials", col_name)

    if "ai_accounts" in tables:
        columns = {col["name"] for col in inspector.get_columns("ai_accounts")}
        for col_name, col_type in _ACCOUNT_PROXY_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE ai_accounts ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to ai_accounts", col_name)
                columns.add(col_name)
        # 回填：凭证级开启 → 账号级开启（幂等）
        if "proxy_enabled" in columns and "ai_account_credentials" in tables:
            cred_cols = {col["name"] for col in inspector.get_columns("ai_account_credentials")}
            if "proxy_enabled" in cred_cols and "account_id" in cred_cols:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE ai_accounts
                            SET proxy_enabled = 1
                            WHERE id IN (
                                SELECT DISTINCT account_id
                                FROM ai_account_credentials
                                WHERE proxy_enabled = 1
                            )
                            AND COALESCE(proxy_enabled, 0) = 0
                            """
                        )
                    )

    if "proxy_key_usages" in tables:
        columns = {col["name"] for col in inspector.get_columns("proxy_key_usages")}
        for col_name, col_type in _PROXY_USAGE_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE proxy_key_usages ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to proxy_key_usages", col_name)

    if "proxy_keys" in tables:
        columns = {col["name"] for col in inspector.get_columns("proxy_keys")}
        for col_name, col_type in _PROXY_KEY_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE proxy_keys ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to proxy_keys", col_name)

    if "proxy_events" in tables:
        columns = {col["name"] for col in inspector.get_columns("proxy_events")}
        for col_name, col_type in _PROXY_EVENT_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE proxy_events ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to proxy_events", col_name)

    if "key_loans" in tables:
        columns = {col["name"] for col in inspector.get_columns("key_loans")}
        for col_name, col_type in _KEY_LOAN_ALIAS_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE key_loans ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to key_loans", col_name)
        # 存量库 ADD COLUMN 不会带 unique；补齐与模型一致的唯一索引
        index_names = {idx["name"] for idx in inspector.get_indexes("key_loans")}
        if "ix_key_loans_alias_key_hash" not in index_names:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_key_loans_alias_key_hash "
                        "ON key_loans (alias_key_hash)"
                    )
                )
            logger.info("Added unique index ix_key_loans_alias_key_hash on key_loans")

    _sqlite_rebuild_proxy_key_usages_nullable_proxy_key(engine)

    Base.metadata.create_all(engine)
    # personamem tables are initialized by assistant_platform (assistant.db), not pulse.db.
