# Cursor 代理 · Pulse 控制面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Pulse 进程内实现 Cursor 代理的控制面：脉冲 key 签发/吊销、三级额度（token 总量/费用/5h 窗口）记账与停用、代理池管理（台账 credential 标记）、内部 API（authorize/pool/usage/events）与管理后台页面。

**Architecture:** 数据面是独立的 Go 代理进程（计划 2 实现），本计划交付的控制面通过 `/api/internal/v1/proxy/*`（共享 token 鉴权）向其提供授权判定、cursor 凭证池下发、用量记账与事件采集；管理员通过 web-admin 新页面管理脉冲 key 与代理池。

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy 2.x / pydantic v2 / pytest / Vue 3 + Element Plus。

**Spec:** `docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md`

**约定：**
- 模型主键遵循现有惯例 `String(36)` + uuid（spec 中"int PK"以此为准）。
- 费用：定价模块产出 USD 浮点，本功能统一换算为 cents（`int(round(usd*100))`）存储与比较。
- reasoning token 并入 output 计价。
- 运行测试：`python -m pytest tests/<file> -v`（仓库根目录执行）。
- 每个 Task 结束后按步骤提交 git。

---

### Task 1: 数据模型与迁移

**Files:**
- Modify: `pulse/storage/models.py`（imports 行 + `AiAccountCredential` 加列 + 文件末尾追加 3 个模型）
- Modify: `pulse/storage/migrate.py`（列字典 + `migrate_schema` 内迁移块）
- Test: `tests/test_proxy_models.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_proxy_models.py`：

```python
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.storage.migrate import migrate_schema
from pulse.storage.models import (
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
    # 模拟遗留库：只有不含 proxy_enabled 的 ai_account_credentials
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE ai_account_credentials (id VARCHAR(36) PRIMARY KEY)")
        )
    migrate_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"proxy_keys", "proxy_key_usages", "proxy_events"} <= tables
    cols = {c["name"] for c in inspect(engine).get_columns("ai_account_credentials")}
    assert "proxy_enabled" in cols


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
    s.close()
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_models.py -v`
Expected: FAIL（`ImportError: cannot import name 'ProxyKey'`）

- [x] **Step 3: 实现模型**

`pulse/storage/models.py` 顶部 imports 中，在 `from sqlalchemy import (...)` 一行里加入 `BigInteger`（按字母序插入现有列表）。

`AiAccountCredential` 模型内（`sync_jitter_sec` 字段之后）追加：

```python
    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
```

文件末尾（`KnowledgeEntry` 之后）追加：

```python
class ProxyKey(Base):
    __tablename__ = "proxy_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_hint: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # unlimited | quota
    token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_5h_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|suspended|revoked
    suspended_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProxyKeyUsage(Base):
    __tablename__ = "proxy_key_usages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    proxy_key_id: Mapped[str] = mapped_column(ForeignKey("proxy_keys.id"), index=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_input: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_output: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_cache_write: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_reasoning: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ProxyEvent(Base):
    __tablename__ = "proxy_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    proxy_key_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [x] **Step 4: 实现迁移**

`pulse/storage/migrate.py` 顶部（`_MEMBER_PORTAL_COLUMNS` 附近）新增：

```python
_CREDENTIAL_PROXY_COLUMNS: dict[str, str] = {
    "proxy_enabled": "BOOLEAN DEFAULT 0",
}
```

`migrate_schema` 内、收尾 `Base.metadata.create_all(engine)` 之前新增块：

```python
    if "ai_account_credentials" in tables:
        columns = {col["name"] for col in inspector.get_columns("ai_account_credentials")}
        for col_name, col_type in _CREDENTIAL_PROXY_COLUMNS.items():
            if col_name not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE ai_account_credentials ADD COLUMN {col_name} {col_type}")
                    )
                logger.info("Added %s column to ai_account_credentials", col_name)
```

（新表无需显式迁移：`migrate_schema` 末尾的 `create_all` 会自动建 `proxy_*` 三表。）

- [x] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_models.py -v`
Expected: 2 passed

- [x] **Step 6: 提交**

```bash
git add pulse/storage/models.py pulse/storage/migrate.py tests/test_proxy_models.py
git commit -m "feat(proxy): proxy key/usage/event models and credential proxy_enabled migration"
```

---

### Task 2: 脉冲 key 生成与哈希（`pulse/proxy/keys.py`）

**Files:**
- Create: `pulse/proxy/__init__.py`（空文件）
- Create: `pulse/proxy/keys.py`
- Test: `tests/test_proxy_keys.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_proxy_keys.py`：

```python
from __future__ import annotations

from pulse.proxy.keys import generate_proxy_key, hash_proxy_key


def test_generate_proxy_key_format():
    plaintext, key_hash, hint = generate_proxy_key()
    assert plaintext.startswith("pk_")
    assert len(plaintext) > 20
    assert key_hash == hash_proxy_key(plaintext)
    assert len(key_hash) == 64
    assert hint == plaintext[:11]


def test_generate_proxy_key_unique():
    a, b = generate_proxy_key(), generate_proxy_key()
    assert a[0] != b[0]
    assert a[1] != b[1]


def test_hash_is_sha256():
    import hashlib

    assert hash_proxy_key("pk_x") == hashlib.sha256(b"pk_x").hexdigest()
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_keys.py -v`
Expected: FAIL（`ModuleNotFoundError: pulse.proxy`）

- [x] **Step 3: 实现**

创建 `pulse/proxy/__init__.py`（空文件）。

创建 `pulse/proxy/keys.py`：

```python
from __future__ import annotations

import hashlib
import secrets


def generate_proxy_key() -> tuple[str, str, str]:
    """返回 (明文 key, sha256 哈希, 展示 hint)。明文仅在创建时返回一次。"""
    plaintext = "pk_" + secrets.token_urlsafe(32)
    return plaintext, hash_proxy_key(plaintext), plaintext[:11]


def hash_proxy_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_keys.py -v`
Expected: 3 passed

- [x] **Step 5: 提交**

```bash
git add pulse/proxy/__init__.py pulse/proxy/keys.py tests/test_proxy_keys.py
git commit -m "feat(proxy): pulse key generation and hashing"
```

---

### Task 3: 授权判定服务（`pulse/proxy/service.py` 第一部分）

**Files:**
- Create: `pulse/proxy/service.py`
- Test: `tests/test_proxy_service.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_proxy_service.py`：

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.proxy import service
from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.storage.models import Base, ProxyKey, ProxyKeyUsage

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = sf()
    yield s
    s.close()


def _add_key(session, plaintext: str, **kwargs) -> ProxyKey:
    key = ProxyKey(
        key_hash=hash_proxy_key(plaintext),
        key_hint=plaintext[:11],
        name=kwargs.pop("name", "k"),
        member_id=kwargs.pop("member_id", "m1"),
        mode=kwargs.pop("mode", "unlimited"),
        **kwargs,
    )
    session.add(key)
    session.flush()
    return key


def test_authorize_unknown_key(session):
    result = service.authorize_status(session, "pk_nope", now=NOW)
    assert result["status"] == "invalid"
    assert result["reason"] == "unknown_key"
    assert result["proxy_key_id"] is None


def test_authorize_ok_unlimited(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    result = service.authorize_status(session, plaintext, now=NOW)
    assert result == {
        "status": "ok",
        "proxy_key_id": key.id,
        "mode": "unlimited",
        "reason": None,
    }


def test_authorize_revoked_and_expired(session):
    p1, _, _ = generate_proxy_key()
    _add_key(session, p1, status="revoked")
    assert service.authorize_status(session, p1, now=NOW)["reason"] == "revoked"

    p2, _, _ = generate_proxy_key()
    _add_key(session, p2, expires_at=NOW - timedelta(seconds=1))
    assert service.authorize_status(session, p2, now=NOW)["reason"] == "expired"


def test_authorize_suspended(session):
    plaintext, _, _ = generate_proxy_key()
    _add_key(session, plaintext, status="suspended", suspended_reason="token_limit_exceeded")
    result = service.authorize_status(session, plaintext, now=NOW)
    assert result["status"] == "suspended"
    assert result["reason"] == "token_limit_exceeded"


def test_authorize_window_limited(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", window_5h_token_limit=100)
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=60, ts=NOW - timedelta(hours=1))
    )
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=50, ts=NOW - timedelta(hours=2))
    )
    session.flush()
    result = service.authorize_status(session, plaintext, now=NOW)
    assert result["status"] == "window_limited"
    assert result["proxy_key_id"] == key.id


def test_window_ignores_old_usage(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", window_5h_token_limit=100)
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=500, ts=NOW - timedelta(hours=6))
    )
    session.flush()
    assert service.window_usage_tokens(session, key.id, now=NOW) == 0
    assert service.authorize_status(session, plaintext, now=NOW)["status"] == "ok"
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_service.py -v`
Expected: FAIL（`ModuleNotFoundError: pulse.proxy.service`）

- [x] **Step 3: 实现 service.py（授权部分）**

创建 `pulse/proxy/service.py`：

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.storage.models import ProxyEvent, ProxyKey, ProxyKeyUsage

WINDOW_5H = timedelta(hours=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_key_by_plaintext(session: Session, plaintext: str) -> ProxyKey | None:
    return session.execute(
        select(ProxyKey).where(ProxyKey.key_hash == hash_proxy_key(plaintext))
    ).scalar_one_or_none()


def create_key(
    session: Session,
    *,
    name: str,
    member_id: str,
    mode: str,
    token_limit: int | None = None,
    cost_limit_cents: int | None = None,
    window_5h_token_limit: int | None = None,
    expires_at: datetime | None = None,
) -> tuple[ProxyKey, str]:
    plaintext, key_hash, hint = generate_proxy_key()
    key = ProxyKey(
        key_hash=key_hash,
        key_hint=hint,
        name=name,
        member_id=member_id,
        mode=mode,
        token_limit=token_limit,
        cost_limit_cents=cost_limit_cents,
        window_5h_token_limit=window_5h_token_limit,
        expires_at=expires_at,
    )
    session.add(key)
    session.flush()
    return key, plaintext


def window_usage_tokens(session: Session, proxy_key_id: str, *, now: datetime | None = None) -> int:
    now = now or _utcnow()
    since = now - WINDOW_5H
    value = session.execute(
        select(func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0)).where(
            ProxyKeyUsage.proxy_key_id == proxy_key_id,
            ProxyKeyUsage.ts >= since,
        )
    ).scalar_one()
    return int(value)


def total_usage(session: Session, proxy_key_id: str) -> tuple[int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        ).where(ProxyKeyUsage.proxy_key_id == proxy_key_id)
    ).one()
    return int(row[0]), int(row[1])


def authorize_status(
    session: Session, plaintext: str, *, now: datetime | None = None
) -> dict:
    now = now or _utcnow()
    key = find_key_by_plaintext(session, plaintext)
    if key is None:
        return {"status": "invalid", "proxy_key_id": None, "mode": None, "reason": "unknown_key"}
    base = {"proxy_key_id": key.id, "mode": key.mode}
    if key.status == "revoked":
        return {"status": "invalid", **base, "reason": "revoked"}
    if key.expires_at is not None and key.expires_at <= now:
        return {"status": "invalid", **base, "reason": "expired"}
    if key.status == "suspended":
        return {"status": "suspended", **base, "reason": key.suspended_reason or "suspended"}
    if key.mode == "quota" and key.window_5h_token_limit is not None:
        used = window_usage_tokens(session, key.id, now=now)
        if used >= key.window_5h_token_limit:
            return {"status": "window_limited", **base, "reason": "window_5h_exceeded"}
    return {"status": "ok", **base, "reason": None}
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_service.py -v`
Expected: 6 passed

- [x] **Step 5: 提交**

```bash
git add pulse/proxy/service.py tests/test_proxy_service.py
git commit -m "feat(proxy): authorize status evaluation with 5h window"
```

---

### Task 4: 记账、额度评估与停用/恢复（`service.py` 第二部分）

**Files:**
- Modify: `pulse/proxy/service.py`
- Test: `tests/test_proxy_service.py`（追加）

- [x] **Step 1: 追加失败测试**

在 `tests/test_proxy_service.py` 末尾追加：

```python
def _usage_item(key: ProxyKey, tokens: dict, model: str = "claude-sonnet-4") -> dict:
    return {
        "proxy_key_id": key.id,
        "credential_id": "cred-1",
        "model": model,
        "tokens": tokens,
        "ts": NOW.isoformat(),
    }


def test_record_usage_computes_total_and_cost(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    result = service.record_usages(
        session,
        [_usage_item(key, {"input": 1_000_000, "output": 100_000, "cache_read": 0,
                           "cache_write": 0, "reasoning": 0})],
        now=NOW,
    )
    assert result["recorded"] == 1
    assert result["suspended"] == []
    usage = session.query(ProxyKeyUsage).one()
    assert usage.total_tokens == 1_100_000
    assert usage.cost_cents > 0  # 定价表命中 claude-* glob 规则
    assert usage.ts == NOW


def test_record_usage_unknown_key_skipped(session):
    result = service.record_usages(
        session, [{"proxy_key_id": "missing", "tokens": {"input": 1}}], now=NOW
    )
    assert result == {"recorded": 0, "suspended": []}


def test_token_limit_suspends(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", token_limit=100)
    result = service.record_usages(
        session, [_usage_item(key, {"input": 150})], now=NOW
    )
    assert result["suspended"] == [key.id]
    session.refresh(key)
    assert key.status == "suspended"
    assert key.suspended_reason == "token_limit_exceeded"
    event = session.query(ProxyEvent).filter_by(event_type="suspended").one()
    assert event.proxy_key_id == key.id


def test_cost_limit_suspends(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", cost_limit_cents=1)
    result = service.record_usages(
        session, [_usage_item(key, {"input": 1_000_000})], now=NOW
    )
    assert result["suspended"] == [key.id]
    session.refresh(key)
    assert key.suspended_reason == "cost_limit_exceeded"


def test_unlimited_mode_never_suspends(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="unlimited", token_limit=1)
    result = service.record_usages(
        session, [_usage_item(key, {"input": 10_000_000})], now=NOW
    )
    assert result["suspended"] == []
    session.refresh(key)
    assert key.status == "active"


def test_resume_after_raising_limit(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", token_limit=100)
    service.record_usages(session, [_usage_item(key, {"input": 150})], now=NOW)
    session.refresh(key)
    assert service.resume_key(session, key) is False  # 仍超限
    key.token_limit = 1000
    assert service.resume_key(session, key) is True
    assert key.status == "active"
    assert key.suspended_reason is None
    assert session.query(ProxyEvent).filter_by(event_type="resumed").count() == 1


def test_record_event(session):
    service.record_event(
        session, event_type="rotation", credential_id="c1", detail="rate_limit"
    )
    session.flush()
    event = session.query(ProxyEvent).one()
    assert event.event_type == "rotation"
    assert event.credential_id == "c1"
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_proxy_service.py -v`
Expected: FAIL（`AttributeError: module 'pulse.proxy.service' has no attribute 'record_usages'`）

- [x] **Step 3: 实现**

`pulse/proxy/service.py` 顶部 imports 追加：

```python
from pulse.pricing.cursor_tables import get_cursor_pricing_table
from pulse.pricing.types import estimate_token_cost
```

文件末尾追加：

```python
_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning")


def estimate_cost_cents(model: str | None, tokens: dict) -> int:
    est = estimate_token_cost(
        model=model or "",
        max_mode=False,
        tokens_input_no_cache=int(tokens.get("input", 0)),
        tokens_input_cache_write=int(tokens.get("cache_write", 0)),
        tokens_cache_read=int(tokens.get("cache_read", 0)),
        tokens_output=int(tokens.get("output", 0)) + int(tokens.get("reasoning", 0)),
        table=get_cursor_pricing_table(),
    )
    if est is None:
        return 0
    return int(round(est.cost_usd * 100))


def record_usages(
    session: Session, items: list[dict], *, now: datetime | None = None
) -> dict:
    now = now or _utcnow()
    recorded = 0
    touched: set[str] = set()
    for item in items:
        key = session.get(ProxyKey, item.get("proxy_key_id") or "")
        if key is None:
            continue
        tokens = item.get("tokens") or {}
        total = sum(int(tokens.get(name, 0)) for name in _TOKEN_FIELDS)
        ts = item.get("ts")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        session.add(
            ProxyKeyUsage(
                proxy_key_id=key.id,
                credential_id=item.get("credential_id"),
                model=item.get("model"),
                tokens_input=int(tokens.get("input", 0)),
                tokens_output=int(tokens.get("output", 0)),
                tokens_cache_read=int(tokens.get("cache_read", 0)),
                tokens_cache_write=int(tokens.get("cache_write", 0)),
                tokens_reasoning=int(tokens.get("reasoning", 0)),
                total_tokens=total,
                cost_cents=estimate_cost_cents(item.get("model"), tokens),
                ts=ts or now,
            )
        )
        recorded += 1
        touched.add(key.id)
    session.flush()
    suspended: list[str] = []
    for key_id in sorted(touched):
        key = session.get(ProxyKey, key_id)
        if key is not None and evaluate_key(session, key):
            suspended.append(key_id)
    return {"recorded": recorded, "suspended": suspended}


def evaluate_key(session: Session, key: ProxyKey) -> bool:
    """额度评估，返回是否发生了新的停用。"""
    if key.mode != "quota" or key.status != "active":
        return False
    total_tokens, total_cost = total_usage(session, key.id)
    if key.token_limit is not None and total_tokens >= key.token_limit:
        suspend_key(session, key, "token_limit_exceeded")
        return True
    if key.cost_limit_cents is not None and total_cost >= key.cost_limit_cents:
        suspend_key(session, key, "cost_limit_exceeded")
        return True
    return False


def suspend_key(session: Session, key: ProxyKey, reason: str) -> None:
    key.status = "suspended"
    key.suspended_reason = reason
    key.updated_at = _utcnow()
    session.add(ProxyEvent(event_type="suspended", proxy_key_id=key.id, detail=reason))


def resume_key(session: Session, key: ProxyKey) -> bool:
    if key.status != "suspended":
        return False
    total_tokens, total_cost = total_usage(session, key.id)
    if key.token_limit is not None and total_tokens >= key.token_limit:
        return False
    if key.cost_limit_cents is not None and total_cost >= key.cost_limit_cents:
        return False
    key.status = "active"
    key.suspended_reason = None
    key.updated_at = _utcnow()
    session.add(ProxyEvent(event_type="resumed", proxy_key_id=key.id))
    return True


def record_event(
    session: Session,
    *,
    event_type: str,
    proxy_key_id: str | None = None,
    credential_id: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        ProxyEvent(
            event_type=event_type,
            proxy_key_id=proxy_key_id,
            credential_id=credential_id,
            detail=detail,
        )
    )


def key_summary(session: Session, key: ProxyKey, *, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    total_tokens, total_cost = total_usage(session, key.id)
    return {
        "id": key.id,
        "key_hint": key.key_hint,
        "name": key.name,
        "member_id": key.member_id,
        "mode": key.mode,
        "token_limit": key.token_limit,
        "cost_limit_cents": key.cost_limit_cents,
        "window_5h_token_limit": key.window_5h_token_limit,
        "status": key.status,
        "suspended_reason": key.suspended_reason,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "total_tokens": total_tokens,
        "total_cost_cents": total_cost,
        "window_5h_tokens": window_usage_tokens(session, key.id, now=now),
    }
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_proxy_service.py -v`
Expected: 13 passed

- [x] **Step 5: 提交**

```bash
git add pulse/proxy/service.py tests/test_proxy_service.py
git commit -m "feat(proxy): usage accounting, quota evaluation, suspend/resume"
```

---

### Task 5: 内部 API（`/api/internal/v1/proxy/*`）

**Files:**
- Create: `pulse/web/internal_proxy_api.py`
- Modify: `pulse/web/app.py`（import + 注册调用）
- Test: `tests/test_web_internal_proxy.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_web_internal_proxy.py`：

```python
from __future__ import annotations

import base64
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig, WebConfig
from pulse.ingestion.crypto import encrypt_secret
from pulse.proxy.keys import generate_proxy_key
from pulse.proxy import service as proxy_service
from pulse.storage.models import (
    AiAccount,
    AiAccountCredential,
    AiPlan,
    AiVendor,
    Base,
    ProxyKeyUsage,
)
from pulse.web.app import create_app
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
        internal=InternalApiConfig(service_token="internal-token"),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="admin", display_name="Admin", password="x")

    vendor = AiVendor(slug="cursor", name="Cursor")
    s.add(vendor)
    s.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        plan_name="Pro",
        slug="pro",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
    )
    s.add(plan)
    s.flush()
    account = AiAccount(
        vendor_id=vendor.id, plan_id=plan.id, account_identifier="acct-1", team_id=team.id
    )
    s.add(account)
    s.flush()
    cred = AiAccountCredential(
        account_id=account.id,
        vendor_id=vendor.id,
        credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-1", TEST_KEY),
        key_hint="cur...y-1",
        bound_by_member_id=owner.id,
        proxy_enabled=True,
    )
    s.add(cred)
    s.commit()
    s.close()
    return {
        "client": TestClient(create_app(config, sf)),
        "sf": sf,
        "cred_id": cred.id,
    }


def _h(token: str = "internal-token") -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_authorize_requires_token(env):
    resp = env["client"].post("/api/internal/v1/proxy/authorize", json={"pulse_key": "pk_x"})
    assert resp.status_code == 401


def test_authorize_unknown_and_ok(env):
    client, sf = env["client"], env["sf"]
    resp = client.post("/api/internal/v1/proxy/authorize", json={"pulse_key": "pk_x"}, headers=_h())
    assert resp.json()["status"] == "invalid"

    s = sf()
    key, plaintext = proxy_service.create_key(s, name="k", member_id="m1", mode="unlimited")
    s.commit()
    s.close()
    resp = client.post("/api/internal/v1/proxy/authorize", json={"pulse_key": plaintext}, headers=_h())
    body = resp.json()
    assert body["status"] == "ok"
    assert body["proxy_key_id"] == key.id


def test_pool_returns_only_enabled_credentials(env):
    resp = env["client"].get("/api/internal/v1/proxy/pool", headers=_h())
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    assert creds == [{"credential_id": env["cred_id"], "api_key": "cursor-key-1"}]


def test_usage_records_and_suspends(env):
    client, sf = env["client"], env["sf"]
    s = sf()
    key, _ = proxy_service.create_key(s, name="k", member_id="m1", mode="quota", token_limit=100)
    s.commit()
    s.close()
    resp = client.post(
        "/api/internal/v1/proxy/usage",
        json={
            "items": [
                {
                    "proxy_key_id": key.id,
                    "credential_id": env["cred_id"],
                    "model": "claude-sonnet-4",
                    "tokens": {"input": 150, "output": 10},
                    "ts": NOW.isoformat(),
                }
            ]
        },
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recorded"] == 1
    assert body["suspended"] == [key.id]
    s = sf()
    assert s.query(ProxyKeyUsage).count() == 1
    s.close()


def test_events_endpoint(env):
    resp = env["client"].post(
        "/api/internal/v1/proxy/events",
        json={"events": [{"event_type": "exhausted", "credential_id": env["cred_id"], "detail": "usage_limit"}]},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"recorded": 1}
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_internal_proxy.py -v`
Expected: FAIL（404 / 路由不存在）

- [x] **Step 3: 实现 internal_proxy_api.py**

创建 `pulse/web/internal_proxy_api.py`：

```python
from __future__ import annotations

import hmac
import logging
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.crypto import decrypt_secret
from pulse.proxy import service as proxy_service
from pulse.storage.models import AiAccountCredential, AiVendor

logger = logging.getLogger(__name__)


class AuthorizeBody(BaseModel):
    pulse_key: str


class UsageItem(BaseModel):
    proxy_key_id: str
    credential_id: str | None = None
    model: str | None = None
    tokens: dict[str, int] = {}
    ts: datetime | None = None


class UsageBody(BaseModel):
    items: list[UsageItem]


class EventItem(BaseModel):
    event_type: str
    proxy_key_id: str | None = None
    credential_id: str | None = None
    detail: str | None = None


class EventsBody(BaseModel):
    events: list[EventItem]


def register_internal_proxy_routes(app, get_db, config) -> None:
    def require_internal_service(
        authorization: Annotated[str | None, Header()] = None,
        x_pulse_internal_token: Annotated[
            str | None, Header(alias="X-Pulse-Internal-Token")
        ] = None,
    ) -> None:
        expected = (config.internal.service_token or "").strip()
        if not expected:
            raise HTTPException(status_code=503, detail="Internal proxy API not configured")
        provided = ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        elif x_pulse_internal_token:
            provided = x_pulse_internal_token.strip()
        if not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.post(
        "/api/internal/v1/proxy/authorize",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_authorize(body: AuthorizeBody, session: Session = Depends(get_db)):
        return proxy_service.authorize_status(session, body.pulse_key)

    @app.get(
        "/api/internal/v1/proxy/pool",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_pool(session: Session = Depends(get_db)):
        rows = (
            session.execute(
                select(AiAccountCredential)
                .join(AiVendor, AiAccountCredential.vendor_id == AiVendor.id)
                .where(
                    AiVendor.slug == "cursor",
                    AiVendor.is_active.is_(True),
                    AiAccountCredential.status == "active",
                    AiAccountCredential.proxy_enabled.is_(True),
                )
            )
            .scalars()
            .all()
        )
        enc_key = (config.credentials.encryption_key or "").strip()
        credentials = []
        for cred in rows:
            try:
                api_key = decrypt_secret(cred.encrypted_value, enc_key)
            except Exception:
                logger.warning("proxy pool: skip credential %s (decrypt failed)", cred.id)
                continue
            credentials.append({"credential_id": cred.id, "api_key": api_key})
        return {"credentials": credentials}

    @app.post(
        "/api/internal/v1/proxy/usage",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_usage(body: UsageBody, session: Session = Depends(get_db)):
        result = proxy_service.record_usages(
            session, [item.model_dump() for item in body.items]
        )
        session.commit()
        return result

    @app.post(
        "/api/internal/v1/proxy/events",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_events(body: EventsBody, session: Session = Depends(get_db)):
        for event in body.events:
            proxy_service.record_event(
                session,
                event_type=event.event_type,
                proxy_key_id=event.proxy_key_id,
                credential_id=event.credential_id,
                detail=event.detail,
            )
        session.commit()
        return {"recorded": len(body.events)}
```

注意：`UsageItem.ts` 经 pydantic 解析为 `datetime`，`record_usages` 中 `isinstance(ts, str)` 分支不命中、直接作为 `ts or now` 使用——与 Task 4 实现兼容。

- [x] **Step 4: 注册路由**

`pulse/web/app.py` 顶部 import 区追加（与现有 import 同组，按字母序）：

```python
from pulse.web.internal_proxy_api import register_internal_proxy_routes
```

`create_app` 注册段（`register_internal_channel_routes(app, config, get_db, _team_repo)` 之后）追加：

```python
    register_internal_proxy_routes(app, get_db, config)
```

- [x] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_web_internal_proxy.py -v`
Expected: 5 passed

- [x] **Step 6: 提交**

```bash
git add pulse/web/internal_proxy_api.py pulse/web/app.py tests/test_web_internal_proxy.py
git commit -m "feat(proxy): internal authorize/pool/usage/events API"
```

---

### Task 6: 权限码 + 管理 API（`/api/v2/proxy-keys`、`/api/v2/proxy-pool`）

**Files:**
- Modify: `pulse/web/permissions.py`（`ALL_PERMISSIONS` + operator 角色）
- Create: `pulse/web/proxy_keys_api.py`
- Modify: `pulse/web/app.py`（import + 注册调用）
- Test: `tests/test_web_proxy_admin.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_web_proxy_admin.py`：

```python
from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, TenantConfig, WebConfig
from pulse.ingestion.crypto import encrypt_secret
from pulse.storage.models import (
    AiAccount,
    AiAccountCredential,
    AiPlan,
    AiVendor,
    Base,
)
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def env():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin", display_name="Admin", password="x"
    )
    vendor = AiVendor(slug="cursor", name="Cursor")
    s.add(vendor)
    s.flush()
    plan = AiPlan(
        vendor_id=vendor.id, plan_name="Pro", slug="pro",
        billing_type="subscription", price_amount=20, price_currency="USD",
    )
    s.add(plan)
    s.flush()
    account = AiAccount(
        vendor_id=vendor.id, plan_id=plan.id, account_identifier="acct-1", team_id=team.id
    )
    s.add(account)
    s.flush()
    cred = AiAccountCredential(
        account_id=account.id, vendor_id=vendor.id, credential_type="api_key",
        encrypted_value=encrypt_secret("cursor-key-1", TEST_KEY), key_hint="cur...y-1",
        bound_by_member_id=owner.id,
    )
    s.add(cred)
    s.commit()
    s.close()
    return {
        "client": TestClient(create_app(config, sf)),
        "config": config,
        "owner": owner,
        "cred_id": cred.id,
        "sf": sf,
    }


def _admin(env) -> dict:
    return {"Authorization": f"Bearer {create_access_token(env['config'], env['owner'])}"}


def test_create_and_list_proxy_key(env):
    resp = env["client"].post(
        "/api/v2/proxy-keys",
        json={"name": "张三的 key", "mode": "quota", "token_limit": 1000000},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plaintext_key"].startswith("pk_")
    assert body["mode"] == "quota"

    resp = env["client"].get("/api/v2/proxy-keys", headers=_admin(env))
    keys = resp.json()
    assert len(keys) == 1
    assert keys[0]["name"] == "张三的 key"
    assert keys[0]["member_name"] == "Admin"
    assert keys[0]["total_tokens"] == 0
    assert "plaintext_key" not in keys[0]
    assert "key_hash" not in keys[0]


def test_create_quota_key_requires_no_limits_is_allowed(env):
    resp = env["client"].post(
        "/api/v2/proxy-keys", json={"name": "k", "mode": "quota"}, headers=_admin(env)
    )
    assert resp.status_code == 200


def test_update_revoke_resume_flow(env):
    client = env["client"]
    key_id = client.post(
        "/api/v2/proxy-keys", json={"name": "k", "mode": "quota", "token_limit": 10},
        headers=_admin(env),
    ).json()["id"]

    resp = client.patch(
        f"/api/v2/proxy-keys/{key_id}", json={"token_limit": 20, "name": "k2"},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "k2"
    assert resp.json()["token_limit"] == 20

    resp = client.post(f"/api/v2/proxy-keys/{key_id}/revoke", headers=_admin(env))
    assert resp.json()["status"] == "revoked"

    # 未 suspend 的 key 调 resume 返回 409
    resp = client.post(f"/api/v2/proxy-keys/{key_id}/resume", headers=_admin(env))
    assert resp.status_code == 409


def test_pool_credentials_toggle(env):
    client = env["client"]
    resp = client.get("/api/v2/proxy-pool/credentials", headers=_admin(env))
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == env["cred_id"]
    assert rows[0]["proxy_enabled"] is False

    resp = client.post(
        f"/api/v2/proxy-pool/credentials/{env['cred_id']}",
        json={"proxy_enabled": True},
        headers=_admin(env),
    )
    assert resp.status_code == 200
    assert resp.json()["proxy_enabled"] is True


def test_usages_endpoint(env):
    client = env["client"]
    key_id = client.post(
        "/api/v2/proxy-keys", json={"name": "k", "mode": "unlimited"}, headers=_admin(env)
    ).json()["id"]
    resp = client.get(f"/api/v2/proxy-keys/{key_id}/usages", headers=_admin(env))
    assert resp.status_code == 200
    assert resp.json() == []


def test_requires_permission(env):
    resp = env["client"].get("/api/v2/proxy-keys")
    assert resp.status_code == 401
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_proxy_admin.py -v`
Expected: FAIL（404）

- [x] **Step 3: 加权限码**

`pulse/web/permissions.py` 的 `ALL_PERMISSIONS` 集合中追加两行（放在 `"knowledge:write",` 之后）：

```python
        "proxy:read",
        "proxy:write",
```

`ROLE_PERMISSIONS["operator"]` 的 frozenset 中追加同样两行（operator 可管理代理；owner 自动继承 `ALL_PERMISSIONS`；auditor 追加 `"proxy:read"`）。

- [x] **Step 4: 实现 proxy_keys_api.py**

创建 `pulse/web/proxy_keys_api.py`：

```python
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy import service as proxy_service
from pulse.storage.models import (
    AiAccountCredential,
    AiVendor,
    Member,
    ProxyKey,
    ProxyKeyUsage,
)
from pulse.web.deps import PortalUser


class CreateProxyKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: str = Field(pattern="^(unlimited|quota)$")
    member_id: str | None = None
    token_limit: int | None = None
    cost_limit_cents: int | None = None
    window_5h_token_limit: int | None = None
    expires_at: datetime | None = None


class UpdateProxyKeyBody(BaseModel):
    name: str | None = None
    token_limit: int | None = None
    cost_limit_cents: int | None = None
    window_5h_token_limit: int | None = None
    expires_at: datetime | None = None


class ToggleCredentialBody(BaseModel):
    proxy_enabled: bool


def _get_key(session: Session, key_id: str) -> ProxyKey:
    key = session.get(ProxyKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="proxy key 不存在")
    return key


def register_proxy_keys_routes(app, get_db, require_capability) -> None:
    @app.get(
        "/api/v2/proxy-keys",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_proxy_keys(session: Session = Depends(get_db)):
        keys = (
            session.execute(select(ProxyKey).order_by(ProxyKey.created_at.desc()))
            .scalars()
            .all()
        )
        member_names = {
            m.id: m.display_name
            for m in session.execute(
                select(Member).where(Member.id.in_({k.member_id for k in keys} or {""}))
            ).scalars()
        }
        rows = []
        for key in keys:
            row = proxy_service.key_summary(session, key)
            row["member_name"] = member_names.get(key.member_id)
            rows.append(row)
        return rows

    @app.post(
        "/api/v2/proxy-keys",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def create_proxy_key(
        body: CreateProxyKeyBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("proxy:write")),
    ):
        member_id = body.member_id or user.member.id
        if session.get(Member, member_id) is None:
            raise HTTPException(status_code=400, detail="归属成员不存在")
        key, plaintext = proxy_service.create_key(
            session,
            name=body.name,
            member_id=member_id,
            mode=body.mode,
            token_limit=body.token_limit,
            cost_limit_cents=body.cost_limit_cents,
            window_5h_token_limit=body.window_5h_token_limit,
            expires_at=body.expires_at,
        )
        session.commit()
        row = proxy_service.key_summary(session, key)
        row["plaintext_key"] = plaintext  # 仅此一次返回
        return row

    @app.patch(
        "/api/v2/proxy-keys/{key_id}",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def update_proxy_key(
        key_id: str, body: UpdateProxyKeyBody, session: Session = Depends(get_db)
    ):
        key = _get_key(session, key_id)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(key, field, value)
        key.updated_at = proxy_service._utcnow()
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.post(
        "/api/v2/proxy-keys/{key_id}/revoke",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def revoke_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_key(session, key_id)
        key.status = "revoked"
        key.updated_at = proxy_service._utcnow()
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.post(
        "/api/v2/proxy-keys/{key_id}/resume",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def resume_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_key(session, key_id)
        if not proxy_service.resume_key(session, key):
            raise HTTPException(status_code=409, detail="额度仍超限或该 key 非 suspended 状态")
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.get(
        "/api/v2/proxy-keys/{key_id}/usages",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_proxy_key_usages(
        key_id: str,
        limit: int = Query(default=50, le=200),
        session: Session = Depends(get_db),
    ):
        _get_key(session, key_id)
        rows = (
            session.execute(
                select(ProxyKeyUsage)
                .where(ProxyKeyUsage.proxy_key_id == key_id)
                .order_by(ProxyKeyUsage.ts.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": u.id,
                "credential_id": u.credential_id,
                "model": u.model,
                "tokens_input": u.tokens_input,
                "tokens_output": u.tokens_output,
                "tokens_cache_read": u.tokens_cache_read,
                "tokens_cache_write": u.tokens_cache_write,
                "tokens_reasoning": u.tokens_reasoning,
                "total_tokens": u.total_tokens,
                "cost_cents": u.cost_cents,
                "ts": u.ts.isoformat() if u.ts else None,
            }
            for u in rows
        ]

    @app.get(
        "/api/v2/proxy-pool/credentials",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_pool_credentials(session: Session = Depends(get_db)):
        rows = (
            session.execute(
                select(AiAccountCredential)
                .join(AiVendor, AiAccountCredential.vendor_id == AiVendor.id)
                .where(AiVendor.slug == "cursor")
                .order_by(AiAccountCredential.bound_at.desc())
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": c.id,
                "account_id": c.account_id,
                "key_hint": c.key_hint,
                "display_name": c.display_name,
                "status": c.status,
                "proxy_enabled": c.proxy_enabled,
            }
            for c in rows
        ]

    @app.post(
        "/api/v2/proxy-pool/credentials/{cred_id}",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def toggle_pool_credential(
        cred_id: str, body: ToggleCredentialBody, session: Session = Depends(get_db)
    ):
        cred = session.get(AiAccountCredential, cred_id)
        if cred is None:
            raise HTTPException(status_code=404, detail="credential 不存在")
        cred.proxy_enabled = body.proxy_enabled
        session.commit()
        return {"id": cred.id, "proxy_enabled": cred.proxy_enabled}
```

- [x] **Step 5: 注册路由**

`pulse/web/app.py` import 区追加：

```python
from pulse.web.proxy_keys_api import register_proxy_keys_routes
```

`create_app` 注册段（Task 5 所加 `register_internal_proxy_routes(app, get_db, config)` 之后）追加：

```python
    register_proxy_keys_routes(app, get_db, require_capability)
```

- [x] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_web_proxy_admin.py -v`
Expected: 6 passed

- [x] **Step 7: 提交**

```bash
git add pulse/web/permissions.py pulse/web/proxy_keys_api.py pulse/web/app.py tests/test_web_proxy_admin.py
git commit -m "feat(proxy): admin API for proxy keys and pool credentials"
```

---

### Task 7: web-admin 页面（脉冲 Key 管理 + 代理池）

**Files:**
- Create: `web-admin/src/views/ProxyKeysView.vue`
- Modify: `web-admin/src/router/index.ts`（MainLayout children 中追加一条）
- Modify: `web-admin/src/layouts/MainLayout.vue`（菜单项 + 图标 import）

- [x] **Step 1: 新建页面**

创建 `web-admin/src/views/ProxyKeysView.vue`：

```vue
<template>
  <div class="proxy-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>代理 Key</h2>
        <p class="desc">Cursor 代理访问凭证：畅享模式不限量记账，限额模式支持 token/费用/5 小时窗口三级额度</p>
      </div>
      <div class="header-actions">
        <el-button v-if="canWrite" type="primary" @click="openCreate">新建 Key</el-button>
      </div>
    </header>

    <el-tabs v-model="tab">
      <el-tab-pane label="脉冲 Key" name="keys">
        <el-table :data="keys" style="width: 100%">
          <el-table-column prop="name" label="名称" min-width="120" />
          <el-table-column prop="key_hint" label="Key" width="130" />
          <el-table-column prop="member_name" label="归属" width="100" />
          <el-table-column label="模式" width="90">
            <template #default="{ row }">
              <el-tag :type="row.mode === 'unlimited' ? 'success' : 'warning'">
                {{ row.mode === 'unlimited' ? '畅享' : '限额' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="用量 / 额度" min-width="200">
            <template #default="{ row }">
              <div v-if="row.mode === 'quota'">
                <div v-if="row.token_limit != null">token: {{ row.total_tokens }} / {{ row.token_limit }}</div>
                <div v-if="row.cost_limit_cents != null">费用: {{ row.total_cost_cents }} / {{ row.cost_limit_cents }} cents</div>
                <div v-if="row.window_5h_token_limit != null">5h窗口: {{ row.window_5h_tokens }} / {{ row.window_5h_token_limit }}</div>
                <div v-if="row.token_limit == null && row.cost_limit_cents == null && row.window_5h_token_limit == null">未配置额度</div>
              </div>
              <span v-else>{{ row.total_tokens }} tokens</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
              <el-tooltip v-if="row.suspended_reason" :content="row.suspended_reason">
                <el-icon><WarningFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openUsages(row)">用量</el-button>
              <el-button v-if="canWrite" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button v-if="canWrite && row.status === 'suspended'" size="small" type="warning" @click="resume(row)">恢复</el-button>
              <el-button v-if="canWrite && row.status !== 'revoked'" size="small" type="danger" @click="revoke(row)">吊销</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="代理池" name="pool">
        <el-table :data="pool" style="width: 100%">
          <el-table-column prop="key_hint" label="Key" width="140" />
          <el-table-column prop="display_name" label="名称" min-width="140" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column label="可用于代理" width="120">
            <template #default="{ row }">
              <el-switch
                :model-value="row.proxy_enabled"
                :disabled="!canWrite"
                @change="(val: boolean) => toggleCredential(row, val)"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="createVisible" title="新建脉冲 Key" width="480px">
      <el-form label-width="120px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" placeholder="例如：张三的 key" />
        </el-form-item>
        <el-form-item label="模式" required>
          <el-radio-group v-model="createForm.mode">
            <el-radio value="unlimited">畅享（不限量）</el-radio>
            <el-radio value="quota">限额</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="createForm.mode === 'quota'">
          <el-form-item label="token 总额度">
            <el-input-number v-model="createForm.token_limit" :min="0" :step="1000000" placeholder="留空不限" />
          </el-form-item>
          <el-form-item label="费用额度(cents)">
            <el-input-number v-model="createForm.cost_limit_cents" :min="0" :step="100" />
          </el-form-item>
          <el-form-item label="5h 窗口 token">
            <el-input-number v-model="createForm.window_5h_token_limit" :min="0" :step="100000" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createdVisible" title="Key 创建成功" width="520px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" title="完整 Key 仅显示这一次，请立即复制保存" />
      <el-input v-model="createdKey" readonly style="margin-top: 12px">
        <template #append>
          <el-button @click="copyCreated">复制</el-button>
        </template>
      </el-input>
      <template #footer>
        <el-button type="primary" @click="createdVisible = false">我已保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑额度" width="480px">
      <el-form label-width="120px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="token 总额度">
          <el-input-number v-model="editForm.token_limit" :min="0" :step="1000000" />
        </el-form-item>
        <el-form-item label="费用额度(cents)">
          <el-input-number v-model="editForm.cost_limit_cents" :min="0" :step="100" />
        </el-form-item>
        <el-form-item label="5h 窗口 token">
          <el-input-number v-model="editForm.window_5h_token_limit" :min="0" :step="100000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="usagesVisible" :title="`用量明细 - ${usagesKeyName}`" size="640px">
      <el-table :data="usages" style="width: 100%">
        <el-table-column prop="ts" label="时间" width="180" />
        <el-table-column prop="model" label="模型" min-width="140" />
        <el-table-column prop="total_tokens" label="tokens" width="110" />
        <el-table-column prop="cost_cents" label="费用(cents)" width="110" />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { WarningFilled } from '@element-plus/icons-vue'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface ProxyKeyRow {
  id: string
  key_hint: string
  name: string
  member_id: string
  member_name: string | null
  mode: string
  token_limit: number | null
  cost_limit_cents: number | null
  window_5h_token_limit: number | null
  status: string
  suspended_reason: string | null
  total_tokens: number
  total_cost_cents: number
  window_5h_tokens: number
}

interface PoolCredential {
  id: string
  key_hint: string
  display_name: string | null
  status: string
  proxy_enabled: boolean
}

interface UsageRow {
  id: string
  model: string | null
  total_tokens: number
  cost_cents: number
  ts: string
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const loading = ref(false)
const saving = ref(false)
const tab = ref('keys')
const keys = ref<ProxyKeyRow[]>([])
const pool = ref<PoolCredential[]>([])
const usages = ref<UsageRow[]>([])

const createVisible = ref(false)
const createdVisible = ref(false)
const createdKey = ref('')
const editVisible = ref(false)
const usagesVisible = ref(false)
const usagesKeyName = ref('')

const createForm = reactive({
  name: '',
  mode: 'unlimited',
  token_limit: null as number | null,
  cost_limit_cents: null as number | null,
  window_5h_token_limit: null as number | null,
})
const editForm = reactive({
  id: '',
  name: '',
  token_limit: null as number | null,
  cost_limit_cents: null as number | null,
  window_5h_token_limit: null as number | null,
})

function statusType(status: string) {
  if (status === 'active') return 'success'
  if (status === 'suspended') return 'warning'
  return 'danger'
}

function statusLabel(status: string) {
  return { active: '正常', suspended: '已停用', revoked: '已吊销' }[status] ?? status
}

async function load() {
  loading.value = true
  try {
    const [keysRes, poolRes] = await Promise.all([
      client.get('/api/v2/proxy-keys'),
      client.get('/api/v2/proxy-pool/credentials'),
    ])
    keys.value = keysRes.data
    pool.value = poolRes.data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.name = ''
  createForm.mode = 'unlimited'
  createForm.token_limit = null
  createForm.cost_limit_cents = null
  createForm.window_5h_token_limit = null
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.name.trim()) {
    ElMessage.error('请填写名称')
    return
  }
  saving.value = true
  try {
    const res = await client.post('/api/v2/proxy-keys', {
      name: createForm.name.trim(),
      mode: createForm.mode,
      token_limit: createForm.mode === 'quota' ? createForm.token_limit : null,
      cost_limit_cents: createForm.mode === 'quota' ? createForm.cost_limit_cents : null,
      window_5h_token_limit: createForm.mode === 'quota' ? createForm.window_5h_token_limit : null,
    })
    createdKey.value = res.data.plaintext_key
    createVisible.value = false
    createdVisible.value = true
    await load()
  } catch {
    ElMessage.error('创建失败')
  } finally {
    saving.value = false
  }
}

async function copyCreated() {
  await navigator.clipboard.writeText(createdKey.value)
  ElMessage.success('已复制')
}

function openEdit(row: ProxyKeyRow) {
  editForm.id = row.id
  editForm.name = row.name
  editForm.token_limit = row.token_limit
  editForm.cost_limit_cents = row.cost_limit_cents
  editForm.window_5h_token_limit = row.window_5h_token_limit
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    await client.patch(`/api/v2/proxy-keys/${editForm.id}`, {
      name: editForm.name,
      token_limit: editForm.token_limit,
      cost_limit_cents: editForm.cost_limit_cents,
      window_5h_token_limit: editForm.window_5h_token_limit,
    })
    editVisible.value = false
    ElMessage.success('已保存')
    await load()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function revoke(row: ProxyKeyRow) {
  await ElMessageBox.confirm(`确定吊销「${row.name}」？吊销后不可恢复。`, '吊销确认', { type: 'warning' })
  await client.post(`/api/v2/proxy-keys/${row.id}/revoke`)
  ElMessage.success('已吊销')
  await load()
}

async function resume(row: ProxyKeyRow) {
  try {
    await client.post(`/api/v2/proxy-keys/${row.id}/resume`)
    ElMessage.success('已恢复')
  } catch {
    ElMessage.error('恢复失败：额度仍超限，请先调高额度')
  }
  await load()
}

async function openUsages(row: ProxyKeyRow) {
  usagesKeyName.value = row.name
  const res = await client.get(`/api/v2/proxy-keys/${row.id}/usages`)
  usages.value = res.data
  usagesVisible.value = true
}

async function toggleCredential(row: PoolCredential, val: boolean) {
  try {
    await client.post(`/api/v2/proxy-pool/credentials/${row.id}`, { proxy_enabled: val })
    row.proxy_enabled = val
  } catch {
    ElMessage.error('操作失败')
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
}
.desc {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
</style>
```

- [x] **Step 2: 注册路由**

`web-admin/src/router/index.ts` 的 MainLayout children 中（`access-requests` 路由块之后）追加：

```ts
        {
          path: 'proxy-keys',
          name: 'proxy-keys',
          component: () => import('@/views/ProxyKeysView.vue'),
          meta: { permission: 'proxy:read', title: '代理 Key' },
        },
```

- [x] **Step 3: 加菜单项**

`web-admin/src/layouts/MainLayout.vue`：

1. 图标 import 语句（`@element-plus/icons-vue` 那行）的解构列表中追加 `Key`。
2. `grp-pulse` 分组的菜单项中追加（放在合适位置，如"工具申请"之后）：

```vue
          <el-menu-item v-if="auth.hasPermission('proxy:read')" index="/proxy-keys">
            <el-icon><Key /></el-icon>
            <span>代理 Key</span>
          </el-menu-item>
```

- [x] **Step 4: 前端构建验证**

Run: `cd web-admin && npm run build`
Expected: 构建成功，无 TS 错误

- [x] **Step 5: 提交**

```bash
git add web-admin/src/views/ProxyKeysView.vue web-admin/src/router/index.ts web-admin/src/layouts/MainLayout.vue
git commit -m "feat(admin): proxy keys management and pool toggle UI"
```

---

### Task 8: 全量回归与冒烟

**Files:** 无新增

- [x] **Step 1: 全量 Python 测试**

Run: `python -m pytest tests/ -x -q`
Expected: 全部通过（含既有约 160 个测试文件；若有与本功能无关的既有失败，记录并在提交信息中说明，不擅自修复）

- [x] **Step 2: 冒烟（可选，本地开发环境）**

```bash
# 启动 web 服务后：
curl -X POST http://127.0.0.1:8080/api/internal/v1/proxy/authorize \
  -H "Authorization: Bearer $PULSE_INTERNAL_SERVICE_TOKEN" \
  -H "Content-Type: application/json" -d '{"pulse_key":"pk_nonexistent"}'
# Expected: {"status":"invalid","proxy_key_id":null,"mode":null,"reason":"unknown_key"}
curl http://127.0.0.1:8080/api/internal/v1/proxy/pool \
  -H "Authorization: Bearer $PULSE_INTERNAL_SERVICE_TOKEN"
# Expected: {"credentials":[...]}
```

- [x] **Step 3: 最终提交（如有冒烟发现的小修复）**

```bash
git add -A && git commit -m "chore(proxy): control-plane smoke fixes"
```

---

## 自审记录

- **Spec 覆盖**：数据模型（§3）→ Task 1；内部 API（§4）→ Task 5；限额记账/停用恢复（§7）→ Task 3-4；管理后台（§8）→ Task 6-7；权限码 → Task 6 Step 3。Go 数据面（§5-6）属计划 2；部署（§11-3）属计划 3。
- **类型一致性**：`authorize_status` 返回 `{status, proxy_key_id, mode, reason}`，Task 5 原样透传；`record_usages` 接受 str 或 datetime 的 `ts`；`key_summary` 字段与前端 `ProxyKeyRow` 一致；`proxy_enabled` 贯穿模型/迁移/管理 API/内部 pool 接口。
- **偏离说明**：spec §3 中 `ProxyKey.id` 的"int PK"按仓库惯例改为 `String(36)` uuid；`ProxyKeyUsage.credential_id` 不设 FK（Go 代理上报的 credential 可能被删除，保留历史归属）。
