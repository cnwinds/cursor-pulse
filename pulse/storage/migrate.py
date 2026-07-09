from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from pulse.storage.models import Base, Team
from personamem.db import init_memory_tables

logger = logging.getLogger(__name__)

_TEAM_ID_TABLES = ("members", "metric_snapshots", "reports", "alert_logs")

_MEMBER_PORTAL_COLUMNS: dict[str, str] = {
    "portal_status": "VARCHAR(16)",
    "portal_role": "VARCHAR(16)",
    "portal_permissions": "JSON",
    "password_hash": "VARCHAR(256)",
    "last_portal_login_at": "DATETIME",
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


def _rebuild_submission_to_ingestion(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "submissions" in tables and "usage_ingestions" not in tables:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS usage_records"))
            conn.execute(text("DROP TABLE IF EXISTS submissions"))
        logger.warning("Dropped legacy submissions/usage_records for ingestion migration")


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

    Base.metadata.create_all(engine)
    init_memory_tables(engine)
