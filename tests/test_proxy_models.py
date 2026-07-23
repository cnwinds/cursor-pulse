from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.storage.migrate import migrate_schema
from pulse.storage.models import (
    AiAccount,
    AiAccountCredential,
    Base,
    ProxyEvent,
    ProxyKey,
    ProxyKeyUsage,
)


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_migrate_creates_proxy_tables_and_credential_column():
    engine = _engine()
    # 模拟遗留库：只有不含 proxy_enabled 的 ai_account_credentials / ai_accounts
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE ai_account_credentials (id VARCHAR(36) PRIMARY KEY)")
        )
        conn.execute(
            text(
                "CREATE TABLE ai_accounts ("
                "id VARCHAR(36) PRIMARY KEY, "
                "account_identifier VARCHAR(256)"
                ")"
            )
        )
    migrate_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"proxy_keys", "proxy_key_usages", "proxy_events"} <= tables
    cols = {c["name"] for c in inspect(engine).get_columns("ai_account_credentials")}
    assert "proxy_enabled" in cols
    acct_cols = {c["name"] for c in inspect(engine).get_columns("ai_accounts")}
    assert "proxy_enabled" in acct_cols
    # 幂等：每次启动都会重跑 migrate_schema，不应报错且 proxy_enabled 列保持存在
    migrate_schema(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("ai_account_credentials")}
    assert "proxy_enabled" in cols
    acct_cols = {c["name"] for c in inspect(engine).get_columns("ai_accounts")}
    assert "proxy_enabled" in acct_cols


def test_migrate_backfills_account_proxy_from_credentials():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE ai_accounts ("
                "id VARCHAR(36) PRIMARY KEY, "
                "account_identifier VARCHAR(256)"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE ai_account_credentials ("
                "id VARCHAR(36) PRIMARY KEY, "
                "account_id VARCHAR(36), "
                "proxy_enabled BOOLEAN DEFAULT 0"
                ")"
            )
        )
        conn.execute(text("INSERT INTO ai_accounts (id, account_identifier) VALUES ('a1', 'acct')"))
        conn.execute(
            text(
                "INSERT INTO ai_account_credentials (id, account_id, proxy_enabled) "
                "VALUES ('c1', 'a1', 1)"
            )
        )
    migrate_schema(engine)
    with engine.connect() as conn:
        enabled = conn.execute(text("SELECT proxy_enabled FROM ai_accounts WHERE id='a1'")).scalar()
    assert enabled in (1, True)


def test_proxy_models_persist():
    engine = _engine()
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = sf()
    key = ProxyKey(
        key_hash="h" * 64,
        key_hint="pk_abcdefgh",
        name="test",
        member_id="m1",
        mode="quota",
        token_limit=1000,
    )
    s.add(key)
    s.flush()
    s.add(
        ProxyKeyUsage(
            proxy_key_id=key.id,
            credential_id="c1",
            model="claude-x",
            tokens_input=10,
            tokens_output=5,
            total_tokens=15,
            cost_cents=3,
        )
    )
    s.add(ProxyEvent(event_type="suspended", proxy_key_id=key.id, detail="token_limit_exceeded"))
    s.commit()
    assert key.status == "active"
    assert key.mode == "quota"
    cred_col = AiAccountCredential.__table__.columns["proxy_enabled"]
    assert cred_col.default is not None
    acct_col = AiAccount.__table__.columns["proxy_enabled"]
    assert acct_col.default is not None
    s.close()
