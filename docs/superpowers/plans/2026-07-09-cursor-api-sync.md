# Cursor API 自动同步 + 统一摄取架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Cursor User API Key 自动同步用量，废弃 Cursor CSV 提交；以 `UsageIngestionService` 统一 API 与手工摄取管道。

**Architecture:** 新增 `pulse/ingestion/`（Adapter + Service）与 `pulse/integrations/cursor_api.py`；`Submission` 全面替换为 `UsageIngestion`；凭证加密存 `ai_account_credentials`；每日调度器拉取事件写入 `usage_records` 并重算 `usage_summaries` + `usage_daily_aggregates`。

**Tech Stack:** Python 3.11+, SQLAlchemy 2, FastAPI, httpx, cryptography (AES-GCM), Vue 3, pytest

**Spec:** [2026-07-09-cursor-api-sync-design.md](../specs/2026-07-09-cursor-api-sync-design.md)

---

## 文件地图

| 路径 | 职责 |
|------|------|
| `pulse/storage/models.py` | `UsageIngestion`, `AiAccountCredential`, `UsageDailyAggregate`；删除 `Submission` |
| `pulse/storage/migrate.py` | 新表 + 列重命名（`submission_id`→`ingestion_id`） |
| `pulse/integrations/cursor_api.py` | Key 兑换、GetCurrentPeriodUsage、GetFilteredUsageEvents |
| `pulse/ingestion/types.py` | `IngestionContext`, `UsageEventDTO`, `IngestionResult` |
| `pulse/ingestion/protocols.py` | `IngestionAdapter` Protocol |
| `pulse/ingestion/registry.py` | 按 vendor + source_type 路由 adapter |
| `pulse/ingestion/credentials.py` | AES 加解密 + `CredentialService` |
| `pulse/ingestion/service.py` | `UsageIngestionService` 唯一写入入口 |
| `pulse/ingestion/daily.py` | `rebuild_daily_aggregates(account_id, dates)` |
| `pulse/ingestion/sync.py` | `CursorSyncService` 编排单次同步 |
| `pulse/ingestion/adapters/cursor_api.py` | Cursor API adapter |
| `pulse/ingestion/adapters/manual_csv.py` | 非 Cursor CSV/XLSX |
| `pulse/ingestion/adapters/manual_vision.py` | 截图 OCR |
| `pulse/ingestion/adapters/manual_text.py` | 文本手工 |
| `pulse/tool_center/ingestion_status.py` | 替代 `submission_status.py` |
| `pulse/tool_center/reminders.py` | Cursor 凭证催办 + 非 Cursor 手工催办 |
| `pulse/channels/reminders/scheduler.py` | 每日 02:00 同步任务 |
| `pulse/web/credentials_api.py` | 绑定/解绑/状态/手动同步 |
| `pulse/web/ingestions_api.py` | 摄取列表 + 审核 |
| `pulse/web/usage_api.py` | 扩展 daily aggregates 查询 |
| `pulse/channels/dingtalk/handler.py` | Key 绑定 + Cursor CSV 拦截 |
| `web-admin/src/views/AccountsView.vue` | Key 绑定 UI |
| `web-admin/src/views/IngestionsView.vue` | 替代 SubmissionsView |
| `tests/test_cursor_api.py` | HTTP client mock 测试 |
| `tests/test_credentials.py` | 加解密 round-trip |
| `tests/test_ingestion_service.py` | 摄取全链路 |
| `tests/test_cursor_sync.py` | 同步编排 |
| `tests/test_ingestion_status.py` | 状态看板 |
| `tests/test_ingestion_reminders.py` | 催办逻辑 |

---

## Phase 1 — 数据模型与凭证加密

### Task 1: 添加 cryptography 依赖与配置

**Files:**
- Modify: `pyproject.toml`
- Modify: `pulse/config.py`
- Modify: `.env.example`

- [ ] **Step 1: 添加依赖**

在 `pyproject.toml` dependencies 增加：

```toml
"cryptography>=42.0",
```

- [ ] **Step 2: 配置项**

在 `pulse/config.py` 的 `EnvSettings` 增加：

```python
pulse_credential_encryption_key: str = ""
```

在 `AppConfig` 增加 `CredentialConfig`：

```python
class CredentialConfig(BaseModel):
    encryption_key: str = ""

class AppConfig(BaseModel):
    # ...
    credentials: CredentialConfig = Field(default_factory=CredentialConfig)
```

在 `load_config()` 中：`cfg.credentials.encryption_key = env.pulse_credential_encryption_key`

- [ ] **Step 3: .env.example**

```
# 32 字节 base64 或 hex，用于加密 Cursor API Key
PULSE_CREDENTIAL_ENCRYPTION_KEY=
```

- [ ] **Step 4: 安装并验证**

```bash
pip install -e ".[dev]"
python -c "from cryptography.fernet import Fernet; print('ok')"
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml pulse/config.py .env.example
git commit -m "chore: add credential encryption key config"
```

---

### Task 2: 凭证加解密工具

**Files:**
- Create: `pulse/ingestion/__init__.py`
- Create: `pulse/ingestion/crypto.py`
- Create: `tests/test_credentials.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_credentials.py
import base64
import os
import pytest
from pulse.ingestion.crypto import decrypt_secret, encrypt_secret, mask_api_key


@pytest.fixture
def enc_key():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_encrypt_decrypt_round_trip(enc_key):
    plain = "crsr_test_key_abcdefghijklmnop"
    blob = encrypt_secret(plain, enc_key)
    assert decrypt_secret(blob, enc_key) == plain


def test_mask_api_key():
    assert mask_api_key("crsr_abc123xyz789") == "crsr_...z789"
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_credentials.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# pulse/ingestion/crypto.py
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _derive_key(raw: str) -> bytes:
    if not raw:
        raise ValueError("PULSE_CREDENTIAL_ENCRYPTION_KEY is required")
    try:
        key = base64.urlsafe_b64decode(raw + "==")
    except Exception:
        key = bytes.fromhex(raw)
    if len(key) not in (16, 24, 32):
        key = hashlib.sha256(raw.encode()).digest()
    return key


def encrypt_secret(plaintext: str, raw_key: str) -> str:
    key = _derive_key(raw_key)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_secret(blob: str, raw_key: str) -> str:
    key = _derive_key(raw_key)
    data = base64.urlsafe_b64decode(blob + "==")
    nonce, ct = data[:12], data[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def mask_api_key(api_key: str) -> str:
    api_key = api_key.strip()
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:5]}...{api_key[-4:]}"
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_credentials.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pulse/ingestion/ tests/test_credentials.py
git commit -m "feat: add AES-GCM credential encryption helpers"
```

---

### Task 3: ORM 模型重构

**Files:**
- Modify: `pulse/storage/models.py`
- Modify: `pulse/storage/migrate.py`

- [ ] **Step 1: 替换 Submission 为 UsageIngestion**

在 `models.py` 中删除 `Submission` 类，新增：

```python
class UsageIngestion(Base):
    __tablename__ = "usage_ingestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), index=True, nullable=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("ai_accounts.id"), index=True, nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("ai_vendors.id"), index=True, nullable=True)
    billing_period: Mapped[str] = mapped_column(String(7), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    triggered_by: Mapped[str] = mapped_column(String(36), default="system")
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_snapshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    member: Mapped[Member | None] = relationship(foreign_keys=[member_id])
    usage_records: Mapped[list[UsageRecord]] = relationship(
        back_populates="ingestion", cascade="all, delete-orphan"
    )
```

- [ ] **Step 2: 新增 AiAccountCredential**

```python
class AiAccountCredential(Base):
    __tablename__ = "ai_account_credentials"
    __table_args__ = (UniqueConstraint("account_id", name="uq_credential_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("ai_vendors.id"), index=True)
    credential_type: Mapped[str] = mapped_column(String(32))
    encrypted_value: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")
    bound_by_member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(16), default="never")
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [ ] **Step 3: 新增 UsageDailyAggregate**

```python
class UsageDailyAggregate(Base):
    __tablename__ = "usage_daily_aggregates"
    __table_args__ = (
        UniqueConstraint("account_id", "event_date", "model", name="uq_daily_agg"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ai_accounts.id"), index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    model: Mapped[str] = mapped_column(String(128))
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 4: 更新 UsageRecord**

```python
# submission_id → ingestion_id
ingestion_id: Mapped[str] = mapped_column(ForeignKey("usage_ingestions.id"), index=True)
external_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
ingestion: Mapped[UsageIngestion] = relationship(back_populates="usage_records")
```

- [ ] **Step 5: 更新 UsageSummary**

```python
# submission_id → latest_ingestion_id
latest_ingestion_id: Mapped[str | None] = mapped_column(ForeignKey("usage_ingestions.id"), nullable=True)
sync_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # api | manual
last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 6: 更新 Member relationship**

```python
ingestions: Mapped[list[UsageIngestion]] = relationship(
    back_populates="member", foreign_keys="UsageIngestion.member_id"
)
```

- [ ] **Step 7: migrate_schema — 开发库直接重建**

因系统未上线，在 `migrate_schema()` 末尾增加：

```python
def _rebuild_submission_to_ingestion(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "submissions" in tables and "usage_ingestions" not in tables:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS usage_records"))
            conn.execute(text("DROP TABLE IF EXISTS submissions"))
        logger.warning("Dropped legacy submissions/usage_records for ingestion migration")
    Base.metadata.create_all(engine)
```

并在 `migrate_schema` 最后调用 `Base.metadata.create_all(engine)` 确保新表创建。

对已有 `usage_records` 若含 `submission_id` 列：

```python
_USAGE_RECORD_INGESTION_COLUMNS = {
    "ingestion_id": "VARCHAR(36)",
    "external_id": "VARCHAR(64)",
}
# 若 submission_id 存在且 ingestion_id 不存在：ADD ingestion_id，后续代码只用 ingestion_id
```

- [ ] **Step 8: 验证模型导入**

```bash
python -c "from pulse.storage.models import UsageIngestion, AiAccountCredential, UsageDailyAggregate; print('ok')"
```

- [ ] **Step 9: Commit**

```bash
git add pulse/storage/models.py pulse/storage/migrate.py
git commit -m "feat: replace Submission with UsageIngestion and add credential tables"
```

---

## Phase 2 — Cursor API 客户端

### Task 4: CursorApiClient

**Files:**
- Create: `pulse/integrations/cursor_api.py`
- Create: `tests/fixtures/cursor_usage_events.json`
- Create: `tests/fixtures/cursor_period_usage.json`
- Create: `tests/test_cursor_api.py`

- [ ] **Step 1: 添加 fixture**

`tests/fixtures/cursor_period_usage.json` — 从 `cursor-usage-api.md` 示例精简。

`tests/fixtures/cursor_usage_events.json`:

```json
{
  "totalUsageEventsCount": 2,
  "usageEventsDisplay": [
    {
      "timestamp": "1783591555915",
      "model": "composer-2.5",
      "kind": "USAGE_EVENT_KIND_INCLUDED_IN_PRO_PLUS",
      "tokenUsage": {
        "inputTokens": 100,
        "outputTokens": 50,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalCents": 3.45
      },
      "chargedCents": 3.45,
      "conversationId": "conv-1"
    }
  ]
}
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_cursor_api.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from pulse.integrations.cursor_api import CursorApiClient, map_usage_event


FIXTURES = Path(__file__).parent / "fixtures"


def test_map_usage_event():
    raw = json.loads((FIXTURES / "cursor_usage_events.json").read_text())["usageEventsDisplay"][0]
    dto = map_usage_event(raw)
    assert dto.model == "composer-2.5"
    assert dto.cost_usd == pytest.approx(0.0345)
    assert dto.external_id


@patch("httpx.Client")
def test_exchange_api_key(mock_client_cls):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"accessToken": "tok", "refreshToken": "ref"}
    mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

    client = CursorApiClient()
    token = client.exchange_api_key("crsr_test")
    assert token == "tok"
```

- [ ] **Step 3: 实现客户端**

```python
# pulse/integrations/cursor_api.py
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api2.cursor.sh"


@dataclass
class UsageEventDTO:
    event_at: datetime
    event_date: datetime.date
    model: str
    kind: str
    tokens_input_cache_write: int
    tokens_input_no_cache: int
    tokens_cache_read: int
    tokens_output: int
    tokens_total: int
    cost_usd: float
    cost_raw: str
    external_id: str
    source_row_hash: str


def map_usage_event(raw: dict) -> UsageEventDTO:
    ts_ms = int(raw["timestamp"])
    event_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    token = raw.get("tokenUsage") or {}
    input_t = int(token.get("inputTokens") or 0)
    output_t = int(token.get("outputTokens") or 0)
    cache_read = int(token.get("cacheReadTokens") or 0)
    cache_write = int(token.get("cacheWriteTokens") or 0)
    cents = float(raw.get("chargedCents") or token.get("totalCents") or 0)
    conv = raw.get("conversationId") or ""
    external_id = hashlib.sha256(f"{raw['timestamp']}:{raw.get('model')}:{conv}".encode()).hexdigest()[:32]
    kind = raw.get("kind") or "unknown"
    cost_raw = "included" if "INCLUDED" in kind else "usage_based"
    return UsageEventDTO(
        event_at=event_at,
        event_date=event_at.date(),
        model=raw.get("model") or "unknown",
        kind=kind,
        tokens_input_cache_write=cache_write,
        tokens_input_no_cache=input_t,
        tokens_cache_read=cache_read,
        tokens_output=output_t,
        tokens_total=input_t + output_t + cache_read + cache_write,
        cost_usd=round(cents / 100.0, 6),
        cost_raw=cost_raw,
        external_id=external_id,
        source_row_hash=external_id,
    )


class CursorApiClient:
    def __init__(self, api_base: str = API_BASE, timeout: float = 30.0):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def exchange_api_key(self, api_key: str) -> str:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.api_base}/auth/exchange_user_api_key",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("accessToken")
            if not token:
                raise ValueError("exchange returned no accessToken")
            return token

    def _post_dashboard(self, token: str, method: str, body: dict) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.api_base}/aiserver.v1.DashboardService/{method}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Connect-Protocol-Version": "1",
                },
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    def get_current_period_usage(self, token: str) -> dict:
        return self._post_dashboard(token, "GetCurrentPeriodUsage", {})

    def iter_filtered_usage_events(
        self,
        token: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        page_size: int = 100,
    ):
        page = 1
        body: dict = {"page": page, "pageSize": page_size}
        if start_ms is not None:
            body["startDate"] = str(start_ms)
        if end_ms is not None:
            body["endDate"] = str(end_ms)
        while True:
            body["page"] = page
            data = self._post_dashboard(token, "GetFilteredUsageEvents", body)
            events = data.get("usageEventsDisplay") or []
            for raw in events:
                yield map_usage_event(raw)
            if not events:
                break
            page += 1
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_cursor_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pulse/integrations/cursor_api.py tests/
git commit -m "feat: add Cursor API client and usage event mapper"
```

---

## Phase 3 — 摄取核心服务

### Task 5: Ingestion 类型与协议

**Files:**
- Create: `pulse/ingestion/types.py`
- Create: `pulse/ingestion/protocols.py`

- [ ] **Step 1: types.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pulse.integrations.cursor_api import UsageEventDTO


@dataclass
class IngestionContext:
    account_id: str
    vendor_id: str
    vendor_slug: str
    billing_period: str
    member_id: str | None
    channel: str
    source_type: str
    triggered_by: str
    raw_file_path: Path | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[UsageEventDTO] = field(default_factory=list)


@dataclass
class IngestionResult:
    ingestion_id: str
    event_count: int
    status: str
```

- [ ] **Step 2: protocols.py**

```python
from __future__ import annotations

from typing import Protocol

from pulse.ingestion.types import IngestionContext
from pulse.integrations.cursor_api import UsageEventDTO


class IngestionAdapter(Protocol):
    vendor_slug: str | None
    source_type: str

    def can_handle(self, context: IngestionContext) -> bool: ...
    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]: ...
    def extract_metadata(self, context: IngestionContext) -> dict: ...
    def requires_review(self) -> bool: ...
```

- [ ] **Step 3: Commit**

```bash
git add pulse/ingestion/types.py pulse/ingestion/protocols.py
git commit -m "feat: add ingestion types and adapter protocol"
```

---

### Task 6: UsageIngestionService

**Files:**
- Create: `pulse/ingestion/service.py`
- Create: `pulse/ingestion/daily.py`
- Create: `tests/test_ingestion_service.py`

- [ ] **Step 1: 写失败测试**

使用内存 SQLite + seed 一个 Cursor 账号，调用 service.ingest 写入 events，断言 `usage_records`、`usage_summaries`、`usage_daily_aggregates` 有数据。

- [ ] **Step 2: 实现 service.py 核心逻辑**

```python
class UsageIngestionService:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id

    def ingest(
        self,
        *,
        context: IngestionContext,
        adapter: IngestionAdapter,
        status: str | None = None,
    ) -> IngestionResult:
        events = context.events or adapter.extract_events(context)
        metadata = context.metadata or adapter.extract_metadata(context)
        final_status = status or ("pending_review" if adapter.requires_review() else "confirmed")

        ingestion = UsageIngestion(
            member_id=context.member_id,
            account_id=context.account_id,
            vendor_id=context.vendor_id,
            billing_period=context.billing_period,
            source_type=context.source_type,
            channel=context.channel,
            status=final_status,
            triggered_by=context.triggered_by,
            event_count=len(events),
            metadata_json=metadata,
            raw_snapshot_path=str(context.raw_file_path) if context.raw_file_path else None,
            raw_text=context.raw_text,
            confirmed_at=datetime.now(timezone.utc) if final_status == "confirmed" else None,
        )
        self.session.add(ingestion)
        self.session.flush()

        if final_status == "confirmed":
            self._replace_account_period_records(context.account_id, context.billing_period, ingestion.id)

        for dto in events:
            self.session.add(self._to_usage_record(dto, ingestion.id, context.member_id))

        self.session.flush()
        self._recompute_summary(context.account_id, context.billing_period, ingestion)
        affected_dates = {dto.event_date for dto in events}
        rebuild_daily_aggregates(self.session, context.account_id, affected_dates)
        self.session.commit()
        return IngestionResult(ingestion_id=ingestion.id, event_count=len(events), status=final_status)
```

`_replace_account_period_records`：删除同 account+period 的旧 confirmed ingestion 的 records（与现有 `delete_account_period_submissions` 逻辑等价）。

`_recompute_summary`：复用 `pulse/tool_center/usage.py` 的 `build_account_usage_summary`。

- [ ] **Step 3: daily.py**

```python
def rebuild_daily_aggregates(session: Session, account_id: str, dates: set[date]) -> None:
    if not dates:
        return
    for d in dates:
        session.execute(
            delete(UsageDailyAggregate).where(
                UsageDailyAggregate.account_id == account_id,
                UsageDailyAggregate.event_date == d,
            )
        )
    rows = session.execute(
        select(
            UsageRecord.event_date,
            UsageRecord.model,
            func.count(),
            func.sum(UsageRecord.cost_usd),
            func.sum(UsageRecord.tokens_input_no_cache + UsageRecord.tokens_input_cache_write),
            func.sum(UsageRecord.tokens_output),
            func.sum(UsageRecord.tokens_cache_read),
        )
        .join(UsageIngestion)
        .where(
            UsageIngestion.account_id == account_id,
            UsageIngestion.status == "confirmed",
            UsageRecord.event_date.in_(dates),
        )
        .group_by(UsageRecord.event_date, UsageRecord.model)
    ).all()
    for event_date, model, cnt, cost, ti, to, tcr in rows:
        session.add(
            UsageDailyAggregate(
                account_id=account_id,
                event_date=event_date,
                model=model,
                event_count=int(cnt),
                total_cost_usd=float(cost or 0),
                tokens_input=int(ti or 0),
                tokens_output=int(to or 0),
                tokens_cache_read=int(tcr or 0),
            )
        )
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_ingestion_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pulse/ingestion/service.py pulse/ingestion/daily.py tests/test_ingestion_service.py
git commit -m "feat: add UsageIngestionService with daily aggregates"
```

---

### Task 7: Adapters

**Files:**
- Create: `pulse/ingestion/adapters/__init__.py`
- Create: `pulse/ingestion/adapters/cursor_api.py`
- Create: `pulse/ingestion/adapters/manual_csv.py`
- Create: `pulse/ingestion/adapters/manual_vision.py`
- Create: `pulse/ingestion/adapters/manual_text.py`
- Create: `pulse/ingestion/registry.py`

- [ ] **Step 1: CursorApiAdapter**

```python
class CursorApiAdapter:
    vendor_slug = "cursor"
    source_type = "api_sync"

    def can_handle(self, context: IngestionContext) -> bool:
        return context.vendor_slug == "cursor" and context.source_type == "api_sync"

    def extract_events(self, context: IngestionContext) -> list[UsageEventDTO]:
        return list(context.events)

    def extract_metadata(self, context: IngestionContext) -> dict:
        return context.metadata

    def requires_review(self) -> bool:
        return False
```

- [ ] **Step 2: ManualCsvAdapter — 包装现有 csv_parser**

从 `parse_usage_events_file` 解析，映射为 `UsageEventDTO`；`can_handle` 要求 `vendor_slug != "cursor"`。

- [ ] **Step 3: ManualVisionAdapter / ManualTextAdapter**

分别包装 `pulse/tool_center/manual.py` 和 vision 提取逻辑；`requires_review() -> True`。

- [ ] **Step 4: registry.py**

```python
DEFAULT_ADAPTERS = [
    CursorApiAdapter(),
    ManualCsvAdapter(),
    ManualVisionAdapter(),
    ManualTextAdapter(),
]

def resolve_adapter(context: IngestionContext, adapters=None) -> IngestionAdapter:
    for adapter in adapters or DEFAULT_ADAPTERS:
        if adapter.can_handle(context):
            return adapter
    raise ValueError(f"no adapter for {context.vendor_slug}/{context.source_type}")
```

- [ ] **Step 5: Commit**

```bash
git add pulse/ingestion/adapters/ pulse/ingestion/registry.py
git commit -m "feat: add ingestion adapters for cursor api and manual sources"
```

---

### Task 8: CredentialService + CursorSyncService

**Files:**
- Create: `pulse/ingestion/credentials.py`
- Create: `pulse/ingestion/sync.py`
- Create: `tests/test_cursor_sync.py`

- [ ] **Step 1: CredentialService**

```python
class CredentialService:
    def __init__(self, session: Session, encryption_key: str):
        self.session = session
        self.encryption_key = encryption_key

    def bind_cursor_api_key(self, *, account_id: str, api_key: str, member_id: str) -> AiAccountCredential:
        account = self.session.get(AiAccount, account_id)
        if not account:
            raise ValueError("account not found")
        client = CursorApiClient()
        client.exchange_api_key(api_key)  # validate
        encrypted = encrypt_secret(api_key, self.encryption_key)
        cred = self.session.scalar(
            select(AiAccountCredential).where(AiAccountCredential.account_id == account_id)
        )
        if cred:
            cred.encrypted_value = encrypted
            cred.key_hint = mask_api_key(api_key)
            cred.status = "active"
            cred.bound_by_member_id = member_id
            cred.bound_at = datetime.now(timezone.utc)
        else:
            cred = AiAccountCredential(
                account_id=account_id,
                vendor_id=account.vendor_id,
                credential_type="cursor_api_key",
                encrypted_value=encrypted,
                key_hint=mask_api_key(api_key),
                bound_by_member_id=member_id,
            )
            self.session.add(cred)
        self.session.commit()
        return cred

    def decrypt_api_key(self, cred: AiAccountCredential) -> str:
        return decrypt_secret(cred.encrypted_value, self.encryption_key)

    def revoke(self, account_id: str) -> None:
        cred = self.session.scalar(
            select(AiAccountCredential).where(AiAccountCredential.account_id == account_id)
        )
        if not cred:
            return
        cred.status = "revoked"
        cred.encrypted_value = ""
        cred.sync_enabled = False
        self.session.commit()
```

- [ ] **Step 2: CursorSyncService**

```python
class CursorSyncService:
  def sync_account(self, account_id: str, *, channel: str = "scheduler") -> IngestionResult:
      cred = ...  # load active credential
      api_key = credential_service.decrypt_api_key(cred)
      token = client.exchange_api_key(api_key)
      period_usage = client.get_current_period_usage(token)
      start_ms = int(period_usage["billingCycleStart"]) if cred.last_sync_at is None else int(cred.last_sync_at.timestamp() * 1000)
      end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
      events = list(client.iter_filtered_usage_events(token, start_ms=start_ms, end_ms=end_ms))
      # 按 event 日期拆分到 billing_period(s)，调用 UsageIngestionService per period
      cred.last_sync_at = datetime.now(timezone.utc)
      cred.last_sync_status = "success"
```

**原子性**：在 `try/except` 中，任何异常 → `session.rollback()`，`cred.last_sync_status = "failed"`，`cred.last_sync_error = str(e)`，不写 ingestion。

- [ ] **Step 3: 测试 mock CursorApiClient**

- [ ] **Step 4: Commit**

```bash
git add pulse/ingestion/credentials.py pulse/ingestion/sync.py tests/test_cursor_sync.py
git commit -m "feat: add credential binding and cursor sync orchestration"
```

---

## Phase 4 — 全局 Submission → Ingestion 替换

### Task 9: 批量重命名与 repository 迁移

**Files:**
- Modify: `pulse/storage/repository.py` — 删除 `save_submission` 等，改为薄包装调用 `UsageIngestionService`
- Modify: 所有引用 `Submission` 的文件（见下方清单）

- [ ] **Step 1: 全局搜索替换**

```bash
rg -l "Submission|submission_id|save_submission|save_csv_submission|save_manual_submission" pulse/ tests/ web-admin/
```

逐文件替换：

| 文件 | 动作 |
|------|------|
| `pulse/aggregate/engine.py` | `Submission` → `UsageIngestion`，`submission_id` → `ingestion_id` |
| `pulse/tool_center/repository.py` | `delete_account_period_submissions` → `delete_account_period_ingestions` |
| `pulse/tool_center/manual.py` | 调用 `UsageIngestionService` |
| `pulse/channels/dingtalk/handler.py` | 同上 |
| `pulse/channels/commands.py` | `confirm_submission` → `confirm_ingestion` |
| `pulse/cli.py` | `save_csv_submission` → ingestion CLI |
| `pulse/pricing/reprice.py` | ingestion_id |
| `pulse/query/engine.py` | ingestion_id |
| `pulse/chat/admin_tools.py` | ingestion 术语 |
| `pulse/export/exporter.py` | ingestion_id |
| `pulse/web/dashboard_api.py` | pending_review ingestions |
| `pulse/web/submission_status_api.py` | 重命名为 `ingestion_status_api.py` |
| `pulse/web/usage_api.py` | ingestion 字段 |
| `pulse/web/app.py` | 路由注册 |
| `pulse/channels/pending_submission.py` | 重命名为 `pending_credential_bind.py`（Cursor）+ `pending_ingestion.py`（手工） |

- [ ] **Step 2: repository.py 保留兼容薄层（可选）**

```python
def save_csv_ingestion(self, ...):
    from pulse.ingestion.service import UsageIngestionService
    ...
```

- [ ] **Step 3: 运行全量测试，逐个修复**

```bash
pytest tests/ -v --tb=short 2>&1 | head -100
```

- [ ] **Step 4: Commit**

```bash
git add pulse/ tests/
git commit -m "refactor: replace Submission with UsageIngestion across codebase"
```

---

### Task 10: 更新种子数据

**Files:**
- Modify: `pulse/tool_center/seed.py`

- [ ] **Step 1: Cursor plans 的 usage_submit_methods**

```python
["api_key"],  # 替换 ["csv_export", "screenshot"]
```

- [ ] **Step 2: 非 Cursor plans 保持不变**

- [ ] **Step 3: Commit**

```bash
git add pulse/tool_center/seed.py
git commit -m "chore: set cursor plans to api_key submit method"
```

---

## Phase 5 — 催办、状态看板、调度器

### Task 11: ingestion_status 模块

**Files:**
- Create: `pulse/tool_center/ingestion_status.py`
- Delete: `pulse/tool_center/submission_status.py`
- Create: `tests/test_ingestion_status.py`
- Modify: `pulse/web/ingestion_status_api.py`（由 submission_status_api 重命名）

- [ ] **Step 1: 实现双轨状态**

```python
def resolve_account_ingestion_status(account, period, credential, summary, pending_ingestion) -> str:
    if account.vendor.slug == "cursor":
        if not credential or credential.status != "active":
            return "no_credential"
        if credential.last_sync_status == "failed":
            return "sync_failed"
        if credential.last_sync_at and credential.last_sync_at < utcnow() - timedelta(hours=36):
            return "sync_stale"
        return "synced"
    if pending_ingestion:
        return "manual_pending"
    if summary:
        return "manual_submitted"
    return "unsubmitted"
```

- [ ] **Step 2: 迁移测试自 `test_submission_status.py`**

- [ ] **Step 3: Commit**

```bash
git add pulse/tool_center/ingestion_status.py pulse/web/ingestion_status_api.py tests/test_ingestion_status.py
git rm pulse/tool_center/submission_status.py pulse/web/submission_status_api.py
git commit -m "feat: add dual-track ingestion status for cursor api and manual vendors"
```

---

### Task 12: 催办逻辑改造

**Files:**
- Modify: `pulse/tool_center/reminders.py`
- Create: `tests/test_ingestion_reminders.py`
- Modify: `pulse/channels/reminders/scheduler.py`

- [ ] **Step 1: 扩展 build_daily_nudge_targets**

```python
@dataclass
class NudgeTarget:
    kind: str  # primary_member | admin_no_primary | sync_failed | no_credential
    account: AiAccount
    member: Member | None = None
```

Cursor 账号：
- `no_credential` → 催主使用人绑定 Key
- `sync_failed` → 催检查 Key
- `synced` → 跳过

非 Cursor：沿用 `get_unsubmitted_accounts`。

- [ ] **Step 2: 更新催办文案**

`send_collection_start` 改为：
- Cursor 账号：「请绑定 API Key，无需再上传 CSV」
- 其他：保留手工提交说明

- [ ] **Step 3: 每日 02:00 同步任务**

在 `scheduler.py` 的 `ReminderService` 或新建 `SyncScheduler`：

```python
def run_daily_cursor_sync(self):
    with self.session_factory() as session:
        creds = session.scalars(
            select(AiAccountCredential).where(
                AiAccountCredential.status == "active",
                AiAccountCredential.sync_enabled.is_(True),
            )
        ).all()
        sync = CursorSyncService(session, self.config.credentials.encryption_key)
        for cred in creds:
            try:
                sync.sync_account(cred.account_id, channel="scheduler")
            except Exception:
                logger.exception("cursor sync failed for %s", cred.account_id)
```

注册 cron：`02:00` timezone from config.

- [ ] **Step 4: 测试 + Commit**

```bash
pytest tests/test_ingestion_reminders.py -v
git commit -m "feat: cursor credential nudges and daily sync scheduler"
```

---

## Phase 6 — Web API

### Task 13: Credentials API

**Files:**
- Create: `pulse/web/credentials_api.py`
- Modify: `pulse/web/app.py`
- Modify: `pulse/web/schemas.py`

- [ ] **Step 1: 端点实现**

```python
@router.post("/api/v2/accounts/{account_id}/credentials")
def bind_credential(account_id: str, body: BindCredentialRequest, ...):
    # 权限：主使用人或 admin
    cred = CredentialService(...).bind_cursor_api_key(...)
    CursorSyncService(...).sync_account(account_id, channel="web")
    return {"key_hint": cred.key_hint, "status": cred.status}

@router.delete("/api/v2/accounts/{account_id}/credentials")
def revoke_credential(...): ...

@router.get("/api/v2/accounts/{account_id}/credentials")
def get_credential_status(...):  # 不返回 encrypted_value

@router.post("/api/v2/accounts/{account_id}/sync")
def trigger_sync(...):  # admin only
```

- [ ] **Step 2: 注册路由**

- [ ] **Step 3: Commit**

```bash
git add pulse/web/credentials_api.py pulse/web/app.py pulse/web/schemas.py
git commit -m "feat: add credentials bind/revoke/sync REST API"
```

---

### Task 14: Ingestions API

**Files:**
- Create: `pulse/web/ingestions_api.py`
- Modify: `pulse/web/usage_api.py` — 增加 `GET /api/v2/accounts/{id}/usage/daily`
- Delete: `pulse/web/submission_status_api.py`（已在 Task 11 处理）

- [ ] **Step 1: ingestions 列表 + 审核**

```python
GET  /api/v2/ingestions?period=2026-07&status=pending_review
POST /api/v2/ingestions/{id}/confirm
POST /api/v2/ingestions/{id}/reject
```

`confirm` 逻辑迁移自 `repository.confirm_submission`。

- [ ] **Step 2: daily usage 查询**

```python
GET /api/v2/accounts/{id}/usage/daily?start=2026-07-01&end=2026-07-31
# 返回 usage_daily_aggregates 行
```

- [ ] **Step 3: 更新 web 测试**

修改 `tests/test_web_dashboard.py` 等。

- [ ] **Step 4: Commit**

```bash
git add pulse/web/
git commit -m "feat: add ingestions API and daily usage aggregates endpoint"
```

---

## Phase 7 — 钉钉 Bot

### Task 15: Key 绑定指令

**Files:**
- Modify: `pulse/channels/commands.py`
- Modify: `pulse/channels/dingtalk/handler.py`

- [ ] **Step 1: 解析绑定指令**

```python
BIND_CURSOR_RE = re.compile(
    r"^绑定\s*cursor(?:\s+(?P<email>\S+@\S+))?\s+(?:key\s+)?(?P<key>crsr_\S+)$",
    re.I,
)
UNBIND_CURSOR_RE = re.compile(r"^解绑\s*cursor(?:\s+(?P<email>\S+@\S+))?$", re.I)
```

- [ ] **Step 2: handler 中处理绑定/解绑**

匹配账号 → `CredentialService.bind` → `CursorSyncService.sync` → 脱敏回复。

- [ ] **Step 3: Cursor CSV 拦截**

在文件处理分支：

```python
if vendor.slug == "cursor":
    return "该 Cursor 账号已支持 API 自动同步，请私聊发送：绑定 cursor key crsr_..."
```

- [ ] **Step 4: 更新 `/待审` 为 ingestion 待审（仅手工厂商）**

- [ ] **Step 5: Commit**

```bash
git add pulse/channels/
git commit -m "feat: dingtalk cursor api key bind/unbind and block csv upload"
```

---

## Phase 8 — Web Admin 前端

### Task 16: AccountsView 凭证 UI

**Files:**
- Modify: `web-admin/src/views/AccountsView.vue`

- [ ] **Step 1: 账号详情抽屉增加**

- 绑定状态徽章（`no_credential` / `synced` / `sync_failed`）
- API Key 输入框 + 绑定按钮
- 解绑按钮
- 上次同步时间
- 管理员「立即同步」按钮

- [ ] **Step 2: 调用新 API**

```typescript
POST /api/v2/accounts/${id}/credentials  { api_key: "crsr_..." }
DELETE /api/v2/accounts/${id}/credentials
POST /api/v2/accounts/${id}/sync
```

- [ ] **Step 3: Commit**

```bash
git add web-admin/src/views/AccountsView.vue
git commit -m "feat(web): cursor api key bind UI on accounts page"
```

---

### Task 17: IngestionsView

**Files:**
- Rename: `web-admin/src/views/SubmissionsView.vue` → `IngestionsView.vue`
- Modify: `web-admin/src/router/index.ts`
- Modify: `web-admin/src/layouts/MainLayout.vue`

- [ ] **Step 1: 更新路由 `/ingestions`**

- [ ] **Step 2: 列表字段**

`source_type`, `channel`, `status`, `event_count`, `ingested_at`；筛选 `pending_review`（手工厂商）

- [ ] **Step 3: PendingApprovalView 仅显示 manual_* source_type**

- [ ] **Step 4: Commit**

```bash
git add web-admin/
git commit -m "feat(web): rename submissions to ingestions views"
```

---

## Phase 9 — 清理与验收

### Task 18: 删除废弃代码

- [ ] 删除 `pulse/channels/pending_submission.py` 中 Cursor 账号选择流程（如已被 credential 绑定替代）
- [ ] 删除 `handler.py` 中 Cursor period_split / account_pick 用于 CSV 的分支
- [ ] 更新 `README.md` 和 `docs/PRD-v2-ai-tool-center.md` §6.2 Cursor 提交方式
- [ ] 将 `cursor-usage-api.md` 加入仓库跟踪（如尚未）

```bash
git add cursor-usage-api.md docs/
git commit -m "docs: update PRD for cursor api key ingestion"
```

---

### Task 19: 全量测试验收

- [ ] **Step 1: 全量 pytest**

```bash
pytest tests/ -v
```

Expected: 全绿

- [ ] **Step 2: 手动冒烟**

```bash
# 设置加密密钥
export PULSE_CREDENTIAL_ENCRYPTION_KEY=$(python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")

pulse init-v2 --seed
pulse web  # 绑定测试 Key（可用 mock）
```

- [ ] **Step 3: 最终 Commit**

```bash
git commit -m "chore: cursor api sync implementation complete"
```

---

## Spec 覆盖自检

| Spec 章节 | 对应 Task |
|-----------|-----------|
| §2 统一摄取架构 | Task 5–7 |
| §3 数据模型 | Task 3 |
| §4.1 Key 绑定 | Task 8, 13, 15, 16 |
| §4.2 每日同步 | Task 8, 12 |
| §4.3 手工厂商 | Task 7, 9 |
| §4.4 CSV 拦截 | Task 15 |
| §5 催办/状态 | Task 11, 12 |
| §6 API/前端 | Task 13–17 |
| §7 安全 | Task 1–2, 8 |
| §8 错误处理 | Task 8 (原子 sync) |
| §9 删除/重构 | Task 9, 18 |
| §10 测试 | 各 Task 内嵌测试 + Task 19 |

无 TBD / 占位符。

---

## 执行方式

**Plan 已保存至** `docs/superpowers/plans/2026-07-09-cursor-api-sync.md`。

两种执行方式：

1. **Subagent-Driven（推荐）** — 每个 Task 派发独立 subagent，任务间做 review，迭代快
2. **Inline Execution** — 在本会话按 Task 顺序直接实现，阶段性 checkpoint 给你确认

你选哪种？
