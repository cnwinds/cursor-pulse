# Assistant Platform Phase 0 — 服务地基与消息旁路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立同仓独立的 `assistant_platform` 服务与 Assistant Store，把钉钉消息以旁路方式幂等入账，同时修复空管理员列表=全员管理员的安全漏洞；旧 handler 仍负责实际回复。

**Architecture:** 新增顶层包 `assistant_platform`（独立 SQLite `data/assistant.db`、独立 FastAPI、独立进程）。Pulse 钉钉 handler 在业务处理前把标准事件 POST 到 Assistant；重复 `channel+channel_message_id` 不二次入账。本阶段不接管回复、不做能力注册表、不迁记忆。

**Tech Stack:** Python 3.11+, SQLAlchemy 2, FastAPI/uvicorn, httpx, pydantic, pytest

**Spec:** [2026-07-14-assistant-platform-design.md](../specs/2026-07-14-assistant-platform-design.md) §4.3 子项目 1、§5.1、§6.1、§8.1、§15.1（仅 `POST /events/messages`）、§17.1、§20 阶段 0

**Out of scope（禁止混入本计划）:** Capability Registry、会话接管、personamem 迁移、评审、Prompt Studio、进化发布

---

## 文件地图

| 路径 | 职责 |
|------|------|
| `assistant_platform/__init__.py` | 包入口 |
| `assistant_platform/config.py` | Assistant 独立配置（DB URL、host/port、team/assistant id） |
| `assistant_platform/domain/events.py` | `IncomingMessageEvent` 值对象 |
| `assistant_platform/domain/identity.py` | 常量 `DEFAULT_ASSISTANT_ID` |
| `assistant_platform/secrets/redact.py` | Cursor Key / 凭证模式识别与脱敏 |
| `assistant_platform/storage/models.py` | Assistant Store ORM |
| `assistant_platform/storage/db.py` | `init_assistant_db` |
| `assistant_platform/storage/repository.py` | 幂等入账、审计、outbox |
| `assistant_platform/ingest/service.py` | `EventIngestService` |
| `assistant_platform/contracts/provider.py` | Pulse Capability Provider 契约类型（仅定义，不实现） |
| `assistant_platform/api/app.py` | FastAPI：health + ingest |
| `assistant_platform/app.py` | 进程入口（API + 后台 job 循环桩） |
| `assistant_platform/cli.py` | `python -m assistant_platform` |
| `pulse/channels/admin_gate.py` | 统一 `_is_admin`（空列表=无人是管理员） |
| `pulse/channels/dingtalk/mirror.py` | 把钉钉消息映射为事件并旁路发送 |
| `pulse/channels/dingtalk/handler.py` | 调用 mirror；用新 admin gate |
| `pulse/channels/commands.py` | 用新 admin gate |
| `pulse/query/engine.py` | 用新 admin gate |
| `pulse/config.py` | `AssistantMirrorConfig` |
| `pulse/cli.py` | 可选：`pulse assistant` 转发 |
| `pulse/dev/services.py` | 注册 `assistant` 开发服务 |
| `pyproject.toml` | include `assistant_platform*` |
| `tests/test_admin_gate.py` | 空管理员列表安全 |
| `tests/assistant_platform/test_redact.py` | 脱敏 |
| `tests/assistant_platform/test_ingest.py` | 幂等入账 |
| `tests/assistant_platform/test_api.py` | HTTP ingest API |
| `tests/test_dingtalk_mirror.py` | 旁路客户端 |

---

### Task 1: 修复空管理员列表 = 全员管理员

**Files:**
- Create: `pulse/channels/admin_gate.py`
- Create: `tests/test_admin_gate.py`
- Modify: `pulse/channels/commands.py`
- Modify: `pulse/channels/dingtalk/handler.py`
- Modify: `pulse/query/engine.py`
- Modify: `pulse/app.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_admin_gate.py
from pulse.channels.admin_gate import is_dingtalk_admin


def test_empty_admin_list_means_nobody_is_admin():
    assert is_dingtalk_admin("u1", []) is False
    assert is_dingtalk_admin("u1", set()) is False


def test_listed_user_is_admin():
    assert is_dingtalk_admin("admin1", ["admin1", "admin2"]) is True
    assert is_dingtalk_admin("other", ["admin1"]) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_admin_gate.py -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# pulse/channels/admin_gate.py
from __future__ import annotations


def is_dingtalk_admin(user_id: str, admin_ids: list[str] | set[str] | None) -> bool:
    """钉钉侧管理员判定。

    空列表表示「未配置任何管理员」→ 无人拥有管理员权限。
    旧行为「空列表=全员管理员」已废弃，避免生产误配。
    """
    if not admin_ids:
        return False
    return user_id in set(admin_ids)
```

- [ ] **Step 4: 替换调用点**

在 `pulse/channels/commands.py` 删除本地 `_is_admin`，改为：

```python
from pulse.channels.admin_gate import is_dingtalk_admin as _is_admin
```

在 `pulse/channels/dingtalk/handler.py` 的 `_is_admin`：

```python
def _is_admin(self, user_id: str) -> bool:
    from pulse.channels.admin_gate import is_dingtalk_admin
    return is_dingtalk_admin(user_id, self.pulse_config.admin.dingtalk_user_ids)
```

在 `pulse/query/engine.py` 删除本地 `_is_admin`，改为从 `pulse.channels.admin_gate` 导入同名函数（或 `is_dingtalk_admin as _is_admin`）。

在 `pulse/app.py` 把 warning 改为更严重的提示：

```python
if not config.admin.dingtalk_user_ids:
    logger.error(
        "admin.dingtalk_user_ids 未配置：钉钉侧无人拥有管理员权限。"
        "请在 config.yaml 或 DINGTALK_ADMIN_USER_IDS 中设置至少一个管理员。"
    )
```

- [ ] **Step 5: 跑测试**

Run: `pytest tests/test_admin_gate.py tests/test_chat_service.py tests/test_key_loan_dingtalk.py -v`

Expected: PASS（若有测试依赖「空列表=管理员」，按新语义改断言）

- [ ] **Step 6: Commit**

```bash
git add pulse/channels/admin_gate.py tests/test_admin_gate.py pulse/channels/commands.py pulse/channels/dingtalk/handler.py pulse/query/engine.py pulse/app.py
git commit -m "$(cat <<'EOF'
fix: treat empty dingtalk admin list as no admins

EOF
)"
```

---

### Task 2: 脚手架 `assistant_platform` 包与配置

**Files:**
- Create: `assistant_platform/__init__.py`
- Create: `assistant_platform/config.py`
- Create: `assistant_platform/domain/__init__.py`
- Create: `assistant_platform/domain/identity.py`
- Modify: `pyproject.toml`
- Modify: `pulse/config.py`
- Modify: `.env.example`（若存在对应段落）

- [ ] **Step 1: 包标记与身份常量**

```python
# assistant_platform/__init__.py
"""Cursor Pulse Assistant Platform — independent process & data ownership."""

__version__ = "0.1.0"
```

```python
# assistant_platform/domain/identity.py
DEFAULT_ASSISTANT_ID = "xiaomai"
```

- [ ] **Step 2: Assistant 配置**

```python
# assistant_platform/config.py
from __future__ import annotations

import os
from pydantic import BaseModel, Field


class AssistantConfig(BaseModel):
    assistant_id: str = "xiaomai"
    team_id: str = ""  # 运行时由 Pulse team.id 注入或配置
    team_slug: str = "default"
    database_url: str = "sqlite:///data/assistant.db"
    host: str = "127.0.0.1"
    port: int = 8090
    # 服务间调用共享密钥（Pulse mirror → Assistant API）
    service_token: str = ""


def load_assistant_config() -> AssistantConfig:
    return AssistantConfig(
        assistant_id=os.environ.get("ASSISTANT_ID", "xiaomai"),
        team_id=os.environ.get("ASSISTANT_TEAM_ID", ""),
        team_slug=os.environ.get("PULSE_TEAM_SLUG", "default"),
        database_url=os.environ.get("ASSISTANT_DATABASE_URL", "sqlite:///data/assistant.db"),
        host=os.environ.get("ASSISTANT_HOST", "127.0.0.1"),
        port=int(os.environ.get("ASSISTANT_PORT", "8090")),
        service_token=os.environ.get("ASSISTANT_SERVICE_TOKEN", ""),
    )
```

- [ ] **Step 3: Pulse 侧 mirror 配置**

在 `pulse/config.py` 增加：

```python
class AssistantMirrorConfig(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8090"
    service_token: str = ""
    timeout_seconds: float = 2.0
    fail_open: bool = True  # 旁路失败不阻塞旧 handler
```

在 `AppConfig` 增加字段：

```python
assistant_mirror: AssistantMirrorConfig = Field(default_factory=AssistantMirrorConfig)
```

在 `EnvSettings` 增加：

```python
assistant_mirror_enabled: str = ""
assistant_mirror_base_url: str = ""
assistant_service_token: str = ""
```

在 `load_config` 末尾映射：

```python
if env.assistant_mirror_enabled.lower() in ("1", "true", "yes", "on"):
    cfg.assistant_mirror.enabled = True
elif env.assistant_mirror_enabled.lower() in ("0", "false", "no", "off"):
    cfg.assistant_mirror.enabled = False
if env.assistant_mirror_base_url:
    cfg.assistant_mirror.base_url = env.assistant_mirror_base_url.rstrip("/")
if env.assistant_service_token:
    cfg.assistant_mirror.service_token = env.assistant_service_token
```

- [ ] **Step 4: setuptools 包含包**

`pyproject.toml`：

```toml
include = ["pulse*", "personamem*", "assistant_platform*"]
```

可选依赖 `web` 已含 fastapi/uvicorn，本阶段复用。

- [ ] **Step 5: Commit**

```bash
git add assistant_platform pulse/config.py pyproject.toml .env.example
git commit -m "$(cat <<'EOF'
chore: scaffold assistant_platform package and mirror config

EOF
)"
```

---

### Task 3: 凭证脱敏

**Files:**
- Create: `assistant_platform/secrets/__init__.py`
- Create: `assistant_platform/secrets/redact.py`
- Create: `tests/assistant_platform/__init__.py`
- Create: `tests/assistant_platform/test_redact.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/assistant_platform/test_redact.py
from assistant_platform.secrets.redact import redact_text


def test_redact_cursor_api_key():
    text = "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz012345"
    redacted, refs = redact_text(text)
    assert "crsr_abcdefghijklmnopqrstuvwxyz012345" not in redacted
    assert "crsr_" in redacted or "CURSOR_KEY" in redacted
    assert len(refs) == 1
    assert refs[0]["kind"] == "cursor_api_key"
    assert refs[0]["secret"].startswith("crsr_")


def test_redact_leaves_normal_text():
    text = "帮我看下本月额度"
    redacted, refs = redact_text(text)
    assert redacted == text
    assert refs == []
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/assistant_platform/test_redact.py -v`

Expected: FAIL

- [ ] **Step 3: 实现**

```python
# assistant_platform/secrets/redact.py
from __future__ import annotations

import re
import uuid
from typing import Any

# Cursor User API Key 常见形态；宁可多拦不可漏拦
_CURSOR_KEY_RE = re.compile(r"\b(crsr_[A-Za-z0-9_-]{16,})\b", re.IGNORECASE)


def redact_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """从文本抽出疑似凭证，返回脱敏正文 + secret refs（含明文，仅供 Secret Store）。"""
    refs: list[dict[str, Any]] = []

    def _repl(match: re.Match[str]) -> str:
        secret = match.group(1)
        ref_id = str(uuid.uuid4())
        hint = secret[:8] + "…" + secret[-4:] if len(secret) > 12 else "****"
        refs.append(
            {
                "ref_id": ref_id,
                "kind": "cursor_api_key",
                "secret": secret,
                "hint": hint,
            }
        )
        return f"[CURSOR_KEY:{hint}]"

    redacted = _CURSOR_KEY_RE.sub(_repl, text)
    return redacted, refs
```

Phase 0 **不**实现完整 Secret Store 落盘加密；ingest 时只持久化 `hint`/`ref_id`/`kind`，**丢弃** `secret` 字段（旁路阶段不执行绑 Key）。注释写明：完整 Secret Store 在阶段 1+ 再做。

在 `EventIngestService` 入账前：

```python
redacted, refs = redact_text(raw_text)
safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
# 明文 secret 不写入 Assistant Store
```

- [ ] **Step 4: 跑测试并 commit**

Run: `pytest tests/assistant_platform/test_redact.py -v`

```bash
git add assistant_platform/secrets tests/assistant_platform/test_redact.py
git commit -m "$(cat <<'EOF'
feat(assistant): redact cursor api keys from mirrored text

EOF
)"
```

---

### Task 4: IncomingMessageEvent 与 Assistant Store 模型

**Files:**
- Create: `assistant_platform/domain/events.py`
- Create: `assistant_platform/storage/__init__.py`
- Create: `assistant_platform/storage/models.py`
- Create: `assistant_platform/storage/db.py`

- [ ] **Step 1: 领域事件**

```python
# assistant_platform/domain/events.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class IncomingMessageEvent:
    event_id: str
    channel: str  # dingtalk | web
    channel_message_id: str
    assistant_id: str
    team_id: str
    sender_channel_user_id: str
    sender_display_name: str
    conversation_type: str  # private | group
    conversation_id: str
    reply_endpoint: dict[str, Any] = field(default_factory=dict)
    text_redacted: str = ""
    secret_refs: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    occurred_at: datetime | None = None
    raw_metadata_redacted: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 2: ORM 模型（Phase 0 最小集）**

```python
# assistant_platform/storage/models.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AssistantRow(Base):
    __tablename__ = "ap_assistants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="小脉")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class IncomingEventRow(Base):
    __tablename__ = "ap_incoming_events"
    __table_args__ = (
        UniqueConstraint("channel", "channel_message_id", name="uq_ap_channel_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    channel_message_id: Mapped[str] = mapped_column(String(128))
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    sender_channel_user_id: Mapped[str] = mapped_column(String(128), index=True)
    sender_display_name: Mapped[str] = mapped_column(String(128), default="")
    conversation_type: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    reply_endpoint_json: Mapped[dict] = mapped_column(JSON, default=dict)
    text_redacted: Mapped[str] = mapped_column(Text, default="")
    secret_refs_json: Mapped[list] = mapped_column(JSON, default=list)
    attachments_json: Mapped[list] = mapped_column(JSON, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    raw_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OutboxEventRow(Base):
    __tablename__ = "ap_outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackgroundJobRow(Base):
    __tablename__ = "ap_background_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditEventRow(Base):
    __tablename__ = "ap_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assistant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 3: DB 初始化**

```python
# assistant_platform/storage/db.py
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.storage.models import Base


def make_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_assistant_db(database_url: str) -> sessionmaker[Session]:
    if database_url.startswith("sqlite:///"):
        Path(database_url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
```

- [ ] **Step 4: Commit**

```bash
git add assistant_platform/domain assistant_platform/storage
git commit -m "$(cat <<'EOF'
feat(assistant): add IncomingMessageEvent and Assistant Store schema

EOF
)"
```

---

### Task 5: 幂等入账服务

**Files:**
- Create: `assistant_platform/storage/repository.py`
- Create: `assistant_platform/ingest/__init__.py`
- Create: `assistant_platform/ingest/service.py`
- Create: `tests/assistant_platform/test_ingest.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/assistant_platform/test_ingest.py
import uuid
from datetime import datetime, timezone

from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.ingest.service import EventIngestService
from assistant_platform.storage.db import init_assistant_db


def _event(msg_id: str, text: str = "hello") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id="team-1",
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def test_ingest_is_idempotent_on_channel_message_id():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    first = svc.ingest(_event("m-1", "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz0123"))
    second = svc.ingest(_event("m-1", "绑定 cursor key crsr_abcdefghijklmnopqrstuvwxyz0123"))
    session.commit()
    assert first.created is True
    assert second.created is False
    assert first.event_row_id == second.event_row_id
    assert "crsr_abcdefghijklmnopqrstuvwxyz0123" not in first.text_redacted


def test_different_message_ids_create_two_rows():
    Session = init_assistant_db("sqlite://")
    session = Session()
    svc = EventIngestService(session)
    a = svc.ingest(_event("m-a"))
    b = svc.ingest(_event("m-b"))
    session.commit()
    assert a.created and b.created
    assert a.event_row_id != b.event_row_id
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/assistant_platform/test_ingest.py -v`

Expected: FAIL

- [ ] **Step 3: 实现 repository + service**

```python
# assistant_platform/storage/repository.py
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.storage.models import (
    AssistantRow,
    AuditEventRow,
    BackgroundJobRow,
    IncomingEventRow,
    OutboxEventRow,
)


class AssistantRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure_assistant(self, assistant_id: str, display_name: str = "小脉") -> None:
        row = self.session.get(AssistantRow, assistant_id)
        if row is None:
            self.session.add(AssistantRow(id=assistant_id, display_name=display_name))
            self.session.flush()

    def find_by_channel_message(self, channel: str, channel_message_id: str) -> IncomingEventRow | None:
        return self.session.scalar(
            select(IncomingEventRow).where(
                IncomingEventRow.channel == channel,
                IncomingEventRow.channel_message_id == channel_message_id,
            )
        )

    def add_incoming(self, row: IncomingEventRow) -> IncomingEventRow:
        self.session.add(row)
        self.session.flush()
        return row

    def add_audit(self, *, assistant_id: str, team_id: str, action: str, detail: str = "", meta: dict | None = None) -> None:
        self.session.add(
            AuditEventRow(
                assistant_id=assistant_id,
                team_id=team_id,
                action=action,
                detail=detail,
                meta_json=meta or {},
            )
        )

    def add_outbox(self, *, assistant_id: str, team_id: str, kind: str, payload: dict) -> OutboxEventRow:
        row = OutboxEventRow(
            assistant_id=assistant_id,
            team_id=team_id,
            kind=kind,
            payload_json=payload,
            status="pending",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_job(self, *, job_type: str, payload: dict) -> BackgroundJobRow:
        row = BackgroundJobRow(job_type=job_type, payload_json=payload, status="pending")
        self.session.add(row)
        self.session.flush()
        return row
```

```python
# assistant_platform/ingest/service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.secrets.redact import redact_text
from assistant_platform.storage.models import IncomingEventRow
from assistant_platform.storage.repository import AssistantRepository


@dataclass
class IngestResult:
    created: bool
    event_row_id: str
    text_redacted: str
    duplicate: bool = False


class EventIngestService:
    def __init__(self, session: Session):
        self.repo = AssistantRepository(session)

    def ingest(self, event: IncomingMessageEvent) -> IngestResult:
        self.repo.ensure_assistant(event.assistant_id)

        existing = self.repo.find_by_channel_message(event.channel, event.channel_message_id)
        if existing is not None:
            self.repo.add_audit(
                assistant_id=event.assistant_id,
                team_id=event.team_id,
                action="event.ingest.duplicate",
                detail=event.channel_message_id,
            )
            return IngestResult(
                created=False,
                event_row_id=existing.id,
                text_redacted=existing.text_redacted,
                duplicate=True,
            )

        # 若调用方已脱敏可直接用；再跑一遍保证安全
        text, refs = redact_text(event.text_redacted or "")
        safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
        if event.secret_refs:
            # 合并调用方已有的安全 refs（无 secret 字段）
            for r in event.secret_refs:
                safe_refs.append({k: r[k] for k in ("ref_id", "kind", "hint") if k in r})

        row = IncomingEventRow(
            event_id=event.event_id,
            channel=event.channel,
            channel_message_id=event.channel_message_id,
            assistant_id=event.assistant_id,
            team_id=event.team_id,
            sender_channel_user_id=event.sender_channel_user_id,
            sender_display_name=event.sender_display_name,
            conversation_type=event.conversation_type,
            conversation_id=event.conversation_id,
            reply_endpoint_json=event.reply_endpoint,
            text_redacted=text,
            secret_refs_json=safe_refs,
            attachments_json=event.attachments,
            occurred_at=event.occurred_at or datetime.now(timezone.utc),
            raw_metadata_json=event.raw_metadata_redacted,
        )
        saved = self.repo.add_incoming(row)
        self.repo.add_outbox(
            assistant_id=event.assistant_id,
            team_id=event.team_id,
            kind="event.received",
            payload={"incoming_event_id": saved.id, "channel_message_id": event.channel_message_id},
        )
        self.repo.add_job(
            job_type="noop.phase0",
            payload={"incoming_event_id": saved.id},
        )
        self.repo.add_audit(
            assistant_id=event.assistant_id,
            team_id=event.team_id,
            action="event.ingest.created",
            detail=event.channel_message_id,
            meta={"incoming_event_id": saved.id},
        )
        return IngestResult(created=True, event_row_id=saved.id, text_redacted=text)
```

- [ ] **Step 4: 跑测试并 commit**

Run: `pytest tests/assistant_platform/test_ingest.py -v`

```bash
git add assistant_platform/storage/repository.py assistant_platform/ingest tests/assistant_platform/test_ingest.py
git commit -m "$(cat <<'EOF'
feat(assistant): idempotent incoming event ingest with audit and outbox

EOF
)"
```

---

### Task 5b: Provider 契约骨架（只定义，不接 Pulse）

**Files:**
- Create: `assistant_platform/contracts/__init__.py`
- Create: `assistant_platform/contracts/provider.py`
- Create: `tests/assistant_platform/test_provider_contract.py`

规格阶段 0 要求「定义 Provider 契约」。本任务只落地类型与示例 JSON，**禁止**实现 Pulse `/api/internal/v1/capabilities`。

- [ ] **Step 1: 写契约模块**

```python
# assistant_platform/contracts/provider.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProviderStatus = Literal["succeeded", "failed", "pending", "unknown"]


@dataclass
class CapabilityInvokeRequest:
    invocation_id: str
    idempotency_key: str
    team_id: str
    actor_member_id: str
    capability_key: str
    capability_version: str
    arguments: dict[str, Any] = field(default_factory=dict)
    confirmed_by: str | None = None
    approved_by: str | None = None
    requested_at: str | None = None  # ISO8601


@dataclass
class CapabilityInvokeResult:
    status: ProviderStatus
    user_message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False
    provider_reference: str | None = None
    completed_at: str | None = None
```

- [ ] **Step 2: 最小测试**

```python
# tests/assistant_platform/test_provider_contract.py
from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult


def test_invoke_request_fields():
    req = CapabilityInvokeRequest(
        invocation_id="i1",
        idempotency_key="k1",
        team_id="t",
        actor_member_id="m",
        capability_key="quota.self.read",
        capability_version="1",
    )
    assert req.capability_key == "quota.self.read"


def test_result_unknown_is_valid_status():
    r = CapabilityInvokeResult(status="unknown", user_message="状态待确认")
    assert r.status == "unknown"
```

- [ ] **Step 3: 跑测试并 commit**

Run: `pytest tests/assistant_platform/test_provider_contract.py -v`

```bash
git add assistant_platform/contracts tests/assistant_platform/test_provider_contract.py
git commit -m "$(cat <<'EOF'
feat(assistant): define capability provider contract types for phase 1

EOF
)"
```

---

### Task 6: Assistant FastAPI + 进程入口

**Files:**
- Create: `assistant_platform/api/__init__.py`
- Create: `assistant_platform/api/app.py`
- Create: `assistant_platform/app.py`
- Create: `assistant_platform/cli.py`
- Create: `assistant_platform/__main__.py`
- Create: `tests/assistant_platform/test_api.py`
- Modify: `pulse/cli.py`（可选薄封装）
- Modify: `pulse/dev/services.py`

- [ ] **Step 1: API 应用**

```python
# assistant_platform/api/app.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.config import AssistantConfig
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.ingest.service import EventIngestService


class IncomingEventBody(BaseModel):
    event_id: str
    channel: str
    channel_message_id: str
    assistant_id: str
    team_id: str
    sender_channel_user_id: str
    sender_display_name: str = ""
    conversation_type: str
    conversation_id: str
    reply_endpoint: dict[str, Any] = Field(default_factory=dict)
    text_redacted: str = ""
    secret_refs: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    occurred_at: datetime | None = None
    raw_metadata_redacted: dict[str, Any] = Field(default_factory=dict)


def create_assistant_app(config: AssistantConfig, session_factory: sessionmaker[Session]) -> FastAPI:
    app = FastAPI(title="Assistant Platform", version="0.1.0")

    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def require_service_token(
        authorization: Annotated[str | None, Header()] = None,
        x_assistant_token: Annotated[str | None, Header(alias="X-Assistant-Token")] = None,
    ) -> None:
        expected = config.service_token
        if not expected:
            # 开发便利：未配置 token 时仅允许本机；生产必须配置
            return
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        elif x_assistant_token:
            token = x_assistant_token.strip()
        if token != expected:
            raise HTTPException(status_code=401, detail="invalid service token")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "assistant_platform", "time": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/assistant/v1/events/messages", dependencies=[Depends(require_service_token)])
    def ingest_message(body: IncomingEventBody, session: Session = Depends(get_db)):
        event = IncomingMessageEvent(**body.model_dump())
        result = EventIngestService(session).ingest(event)
        session.commit()
        return {
            "created": result.created,
            "duplicate": result.duplicate,
            "event_row_id": result.event_row_id,
            "text_redacted": result.text_redacted,
        }

    return app
```

- [ ] **Step 2: 进程入口**

```python
# assistant_platform/app.py
from __future__ import annotations

import logging
import threading
import time

import uvicorn

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig, load_assistant_config
from assistant_platform.storage.db import init_assistant_db

logger = logging.getLogger(__name__)


def _job_loop(session_factory, stop_event: threading.Event) -> None:
    """Phase 0：仅认领 noop 任务并标记完成，证明同进程后台循环可用。"""
    from sqlalchemy import select
    from assistant_platform.storage.models import BackgroundJobRow

    while not stop_event.is_set():
        session = session_factory()
        try:
            job = session.scalar(
                select(BackgroundJobRow)
                .where(BackgroundJobRow.status == "pending")
                .order_by(BackgroundJobRow.created_at.asc())
                .limit(1)
            )
            if job is None:
                session.close()
                stop_event.wait(1.0)
                continue
            job.status = "done"
            job.attempts = (job.attempts or 0) + 1
            session.commit()
        except Exception:
            logger.exception("assistant job loop failed")
            session.rollback()
        finally:
            session.close()
        stop_event.wait(0.2)


def run_assistant(config: AssistantConfig | None = None) -> None:
    config = config or load_assistant_config()
    session_factory = init_assistant_db(config.database_url)
    app = create_assistant_app(config, session_factory)
    stop = threading.Event()
    worker = threading.Thread(target=_job_loop, args=(session_factory, stop), daemon=True)
    worker.start()
    logger.info("Assistant Platform listening on %s:%s", config.host, config.port)
    try:
        uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    finally:
        stop.set()
        worker.join(timeout=2)
```

```python
# assistant_platform/cli.py
from __future__ import annotations

import argparse
import logging

from assistant_platform.app import run_assistant
from assistant_platform.config import load_assistant_config


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="assistant-platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Start Assistant Platform API + job loop")
    args = parser.parse_args(argv)
    if args.command == "serve":
        run_assistant(load_assistant_config())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# assistant_platform/__main__.py
from assistant_platform.cli import main

raise SystemExit(main())
```

- [ ] **Step 3: API 测试**

```python
# tests/assistant_platform/test_api.py
import uuid

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db


@pytest.fixture
def client():
    cfg = AssistantConfig(service_token="secret", team_id="t1")
    sf = init_assistant_db("sqlite://")
    app = create_assistant_app(cfg, sf)
    return TestClient(app)


def test_health(client):
    assert client.get("/health").status_code == 200


def test_ingest_requires_token(client):
    body = {
        "event_id": str(uuid.uuid4()),
        "channel": "dingtalk",
        "channel_message_id": "mid-1",
        "assistant_id": "xiaomai",
        "team_id": "t1",
        "sender_channel_user_id": "u1",
        "conversation_type": "private",
        "conversation_id": "u1",
        "text_redacted": "hi",
    }
    assert client.post("/api/assistant/v1/events/messages", json=body).status_code == 401
    ok = client.post(
        "/api/assistant/v1/events/messages",
        json=body,
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200
    assert ok.json()["created"] is True
    again = client.post(
        "/api/assistant/v1/events/messages",
        json=body,
        headers={"X-Assistant-Token": "secret"},
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
```

- [ ] **Step 4: 注册开发服务**

在 `pulse/dev/services.py`：

```python
if service == "assistant":
    return (
        [sys.executable, "-m", "assistant_platform", "serve"],
        root,
        {},
    )
```

并在 `SERVICES` 增加：

```python
"assistant": DevService("assistant", "Assistant Platform", 8090, "http://127.0.0.1:8090"),
```

`DEFAULT_SERVICES` **暂不**自动包含 assistant（避免未配置 token 时干扰日常开发）；文档说明用 `pulse-dev start assistant` 或等价命令启动。

在 `pulse/cli.py` 可选增加：

```python
p_asst = sub.add_parser("assistant", help="Run Assistant Platform")
p_asst.add_argument("assistant_cmd", choices=["serve"])
# handle: from assistant_platform.app import run_assistant; run_assistant()
```

- [ ] **Step 5: 跑测试并 commit**

Run: `pytest tests/assistant_platform/test_api.py -v`

```bash
git add assistant_platform/api assistant_platform/app.py assistant_platform/cli.py assistant_platform/__main__.py tests/assistant_platform/test_api.py pulse/dev/services.py pulse/cli.py
git commit -m "$(cat <<'EOF'
feat(assistant): add FastAPI ingest endpoint and serve entrypoint

EOF
)"
```

---

### Task 7: 钉钉旁路 Mirror（旧 handler 仍回复）

**Files:**
- Create: `pulse/channels/dingtalk/mirror.py`
- Create: `tests/test_dingtalk_mirror.py`
- Modify: `pulse/channels/dingtalk/handler.py`
- Modify: `.env.example` / `config.example.yaml` 文档片段

- [ ] **Step 1: 写失败测试**

```python
# tests/test_dingtalk_mirror.py
from unittest.mock import MagicMock, patch

from pulse.channels.dingtalk.mirror import build_event_from_dingtalk, mirror_dingtalk_message
from pulse.config import AppConfig, AssistantMirrorConfig


def test_build_event_maps_ids():
    incoming = MagicMock()
    incoming.message_id = "msg-9"
    incoming.conversation_type = "1"
    incoming.conversation_id = "cid"
    incoming.sender_staff_id = "staff-1"
    incoming.sender_id = None
    incoming.sender_nick = "Bob"
    cfg = AppConfig()
    event = build_event_from_dingtalk(
        incoming,
        text="hello",
        config=cfg,
        team_id="team-xyz",
        is_group=False,
    )
    assert event.channel == "dingtalk"
    assert event.channel_message_id == "msg-9"
    assert event.sender_channel_user_id == "staff-1"
    assert event.conversation_type == "private"
    assert event.team_id == "team-xyz"


def test_mirror_posts_when_enabled():
    cfg = AppConfig(
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        )
    )
    incoming = MagicMock()
    incoming.message_id = "m1"
    incoming.conversation_type = "1"
    incoming.conversation_id = "c"
    incoming.sender_staff_id = "u"
    incoming.sender_id = "u"
    incoming.sender_nick = "N"
    with patch("pulse.channels.dingtalk.mirror.httpx.Client") as Client:
        client = Client.return_value.__enter__.return_value
        client.post.return_value = MagicMock(status_code=200, json=lambda: {"created": True})
        mirror_dingtalk_message(
            incoming,
            text="hi",
            config=cfg,
            team_id="t1",
            is_group=False,
        )
        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args[0].endswith("/api/assistant/v1/events/messages")
```

- [ ] **Step 2: 实现 mirror**

```python
# pulse/channels/dingtalk/mirror.py
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx

from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.domain.identity import DEFAULT_ASSISTANT_ID
from assistant_platform.secrets.redact import redact_text
from pulse.config import AppConfig

logger = logging.getLogger(__name__)


def build_event_from_dingtalk(
    incoming,
    *,
    text: str,
    config: AppConfig,
    team_id: str,
    is_group: bool,
) -> IncomingMessageEvent:
    redacted, refs = redact_text(text or "")
    safe_refs = [{"ref_id": r["ref_id"], "kind": r["kind"], "hint": r["hint"]} for r in refs]
    sender = incoming.sender_staff_id or incoming.sender_id or ""
    conversation_id = incoming.conversation_id or sender
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(incoming.message_id or uuid.uuid4()),
        assistant_id=DEFAULT_ASSISTANT_ID,
        team_id=team_id,
        sender_channel_user_id=str(sender),
        sender_display_name=str(incoming.sender_nick or sender),
        conversation_type="group" if is_group else "private",
        conversation_id=str(conversation_id),
        reply_endpoint={
            "channel": "dingtalk",
            "conversation_type": "group" if is_group else "private",
            "conversation_id": str(conversation_id),
            "user_id": str(sender),
        },
        text_redacted=redacted,
        secret_refs=safe_refs,
        attachments=[],
        occurred_at=datetime.now(timezone.utc),
        raw_metadata_redacted={
            "conversation_title": getattr(incoming, "conversation_title", None),
        },
    )


def mirror_dingtalk_message(
    incoming,
    *,
    text: str,
    config: AppConfig,
    team_id: str,
    is_group: bool,
) -> None:
    mirror = config.assistant_mirror
    if not mirror.enabled:
        return
    event = build_event_from_dingtalk(
        incoming, text=text, config=config, team_id=team_id, is_group=is_group
    )
    url = f"{mirror.base_url.rstrip('/')}/api/assistant/v1/events/messages"
    headers = {"Content-Type": "application/json"}
    if mirror.service_token:
        headers["Authorization"] = f"Bearer {mirror.service_token}"
    payload = {
        "event_id": event.event_id,
        "channel": event.channel,
        "channel_message_id": event.channel_message_id,
        "assistant_id": event.assistant_id,
        "team_id": event.team_id,
        "sender_channel_user_id": event.sender_channel_user_id,
        "sender_display_name": event.sender_display_name,
        "conversation_type": event.conversation_type,
        "conversation_id": event.conversation_id,
        "reply_endpoint": event.reply_endpoint,
        "text_redacted": event.text_redacted,
        "secret_refs": event.secret_refs,
        "attachments": event.attachments,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "raw_metadata_redacted": event.raw_metadata_redacted,
    }
    try:
        with httpx.Client(timeout=mirror.timeout_seconds) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
    except Exception:
        if mirror.fail_open:
            logger.exception("Assistant mirror failed (fail-open); continuing legacy handler")
            return
        raise
```

- [ ] **Step 3: 接入 handler（不改变回复路径）**

在 `DingTalkChannelHandler._handle_message` 解析出 `user_id` / `text` / `is_group` 后、进入命令/对话分支**之前**，增加：

```python
# 旁路镜像：失败默认不影响旧路径
try:
    from pulse.channels.dingtalk.mirror import mirror_dingtalk_message
    from pulse.tenant.context import team_repository

    session = self.session_factory()
    try:
        team, _repo = team_repository(session, self.pulse_config)
        mirror_dingtalk_message(
            incoming,
            text=text or "",
            config=self.pulse_config,
            team_id=team.id,
            is_group=is_group,
        )
    finally:
        session.close()
except Exception:
    logger.exception("Assistant mirror hook crashed; continuing")
```

注意：无文本的图片/文件消息也要镜像（`text=""`，后续阶段再补 attachment 元数据）。可在图片/文件分支入口同样调用一次，或把 hook 提到 `_handle_message` 开头、在拿到 `is_group` 后立即调用（`text` 可能为空）。

推荐：在 `user_id` 解析完成后立刻 mirror（`text` 已 normalize 或为空），保证所有消息类型都入账。

- [ ] **Step 4: 配置示例**

`.env.example` 增加：

```
ASSISTANT_MIRROR_ENABLED=false
ASSISTANT_MIRROR_BASE_URL=http://127.0.0.1:8090
ASSISTANT_SERVICE_TOKEN=
ASSISTANT_DATABASE_URL=sqlite:///data/assistant.db
ASSISTANT_PORT=8090
```

- [ ] **Step 5: 跑测试并 commit**

Run: `pytest tests/test_dingtalk_mirror.py tests/assistant_platform/ -v`

```bash
git add pulse/channels/dingtalk/mirror.py pulse/channels/dingtalk/handler.py tests/test_dingtalk_mirror.py .env.example
git commit -m "$(cat <<'EOF'
feat(bot): mirror dingtalk messages to assistant platform bypass

EOF
)"
```

---

### Task 8: Phase 0 验收与文档锚点

**Files:**
- Modify: `README.md`（简短「Assistant Platform Phase 0」启动说明，3–6 行）
- Modify: `docs/superpowers/specs/2026-07-14-assistant-platform-design.md` 状态行（可选：标注 Phase 0 计划链接）

- [ ] **Step 1: 全量相关测试**

Run:

```bash
pytest tests/test_admin_gate.py tests/test_dingtalk_mirror.py tests/assistant_platform/ -v
```

Expected: 全部 PASS

- [ ] **Step 2: 手动冒烟（开发者本机）**

1. 终端 A：`python -m assistant_platform serve`
2. `curl http://127.0.0.1:8090/health` → ok
3. 配置 `ASSISTANT_MIRROR_ENABLED=true` 与同一 `ASSISTANT_SERVICE_TOKEN`
4. 启动 bot；发一条钉钉私聊
5. 用 sqlite 查 `data/assistant.db` 的 `ap_incoming_events` 应有一行；重复投递同 message_id 不增行
6. 关闭 mirror 或停 assistant → bot 仍正常回复（fail-open）

- [ ] **Step 3: README 片段**

```markdown
### Assistant Platform（Phase 0 旁路）

```bash
# 终端 1 — Assistant
set ASSISTANT_SERVICE_TOKEN=dev-token
python -m assistant_platform serve

# Pulse .env
ASSISTANT_MIRROR_ENABLED=true
ASSISTANT_MIRROR_BASE_URL=http://127.0.0.1:8090
ASSISTANT_SERVICE_TOKEN=dev-token
```

钉钉消息会旁路入账到 `data/assistant.db`；回复仍由现有 bot handler 处理。
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-07-14-assistant-platform-design.md
git commit -m "$(cat <<'EOF'
docs: document assistant platform phase 0 bypass startup

EOF
)"
```

---

## Phase 0 完成定义

- [ ] 空 `admin.dingtalk_user_ids` 时钉钉侧**无人**是管理员
- [ ] `assistant_platform` 可独立 `serve`，health 正常
- [ ] `POST /api/assistant/v1/events/messages` 幂等（同 channel+message_id）
- [ ] Cursor Key 明文不进入 `ap_incoming_events.text_redacted` / `secret_refs_json`
- [ ] Provider 契约类型已定义（无 Pulse 实现）
- [ ] 钉钉旁路可开关；失败默认 fail-open，旧回复路径不变
- [ ] 入账同时写 audit + outbox + background_job 桩
- [ ] 相关 pytest 全绿

## 计划自检（对照规格阶段 0）

| 规格要求 | 对应任务 |
|----------|----------|
| 独立服务 + Assistant Store | Task 2–6 |
| 统一身份 / 消息契约 | Task 4（事件字段）、Task 7（钉钉映射） |
| Provider 契约 | Task 5b |
| 钉钉旁路镜像、旧 handler 仍回复 | Task 7 |
| 幂等、outbox、基础审计 | Task 5 |
| 修复空管理员列表 | Task 1 |
| 不混入能力中心/会话接管/记忆/评审 | Out of scope 已声明 |

## 下一步（不在本计划）

阶段 1：Capability Registry + Pulse Provider API + `quota.self.read` / `cursor.key.bind` / `guide_image.update`
