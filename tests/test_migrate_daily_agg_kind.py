from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from pulse.storage.migrate import migrate_schema
from pulse.storage.models import Base, UsageDailyAggregate


def _legacy_daily_agg_sql() -> str:
    return """
    CREATE TABLE usage_daily_aggregates (
        id VARCHAR(36) PRIMARY KEY,
        account_id VARCHAR(36) NOT NULL,
        event_date DATE NOT NULL,
        model VARCHAR(128) NOT NULL,
        event_count INTEGER NOT NULL,
        total_cost_usd NUMERIC(12, 4) NOT NULL,
        tokens_input INTEGER NOT NULL,
        tokens_output INTEGER NOT NULL,
        tokens_cache_read INTEGER NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_daily_agg UNIQUE (account_id, event_date, model)
    )
    """


def test_migrate_widens_daily_agg_unique_to_kind_family():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE usage_daily_aggregates"))
        conn.execute(text(_legacy_daily_agg_sql()))
        conn.execute(
            text(
                """
                INSERT INTO usage_daily_aggregates (
                    id, account_id, event_date, model, event_count, total_cost_usd,
                    tokens_input, tokens_output, tokens_cache_read, updated_at
                ) VALUES (
                    'agg-1', 'acct-1', '2026-07-15', 'GLM-5.2', 2, 1.0,
                    10, 20, 0, '2026-07-15T00:00:00Z'
                )
                """
            )
        )

    migrate_schema(engine)

    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("usage_daily_aggregates")}
    assert "kind_family" in columns
    unique_cols = [
        list(c.get("column_names") or [])
        for c in inspector.get_unique_constraints("usage_daily_aggregates")
    ]
    unique_cols.extend(
        list(idx.get("column_names") or [])
        for idx in inspector.get_indexes("usage_daily_aggregates")
        if idx.get("unique")
    )
    assert ["account_id", "event_date", "model", "kind_family"] in unique_cols

    session = sessionmaker(bind=engine)()
    session.add(
        UsageDailyAggregate(
            id="agg-2",
            account_id="acct-1",
            event_date=date(2026, 7, 15),
            model="GLM-5.2",
            kind_family="user_api_key",
            event_count=1,
            total_cost_usd=0,
            tokens_input=5,
            tokens_output=0,
            tokens_cache_read=0,
        )
    )
    session.commit()
    rows = list(
        session.scalars(
            select(UsageDailyAggregate).where(UsageDailyAggregate.account_id == "acct-1")
        )
    )
    families = {row.kind_family for row in rows}
    assert "unknown" in families
    assert "user_api_key" in families
    session.close()
