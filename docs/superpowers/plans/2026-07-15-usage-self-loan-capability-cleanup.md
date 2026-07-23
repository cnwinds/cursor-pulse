# 自助用量 · 借入 Key · 能力清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 聊天「我的用量」按账号+模型+周期展示；「我借的key」命中借入查询；清理重复能力包/分配。

**Architecture:** Intent 层扩借入与用量周期参数；新建 `pulse/tool_center/usage_self.py` 做分账号聚合与文案；`usage.query` 注册专用 handler（自助走新路径，团队 NL 仍委托 `handle_channel_command`）；`commands.py` 同步识别借入/自助用量短语；seed 增加去重后再补齐 items。

**Tech Stack:** Python、SQLAlchemy、pytest、现有 `billing_cycle` / `UsageDailyAggregate` / Capability seed

**Spec:** `docs/superpowers/specs/2026-07-15-usage-self-loan-capability-cleanup-design.md`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `assistant_platform/conversation/intents.py` | 借入模糊匹配；用量 intent 带 `period_mode`/`period` |
| `pulse/tool_center/usage_self.py` | **新建**：周期解析、按账号聚合、格式化回复 |
| `pulse/capabilities/handlers/usage_query.py` | **新建**：`usage.query` 专用 handler |
| `pulse/capabilities/invoke.py` | 注册 `usage.query` → 专用 handler |
| `pulse/channels/commands.py` | 借入短语扩匹配；`looks_like_query` 前拦截自助用量 |
| `assistant_platform/capabilities/seed.py` | 去重重复 pack/assignment |
| `tests/test_command_cutover.py` | Intent 用例 |
| `tests/test_usage_self.py` | **新建**：聚合与文案 |
| `tests/test_capability_usage_query.py` | **新建**：handler 冒烟 |
| `tests/assistant_platform/test_capability_registry_seed.py` | 对齐全量 catalog + 去重断言 |
| `tests/assistant_platform/test_capability_resolve.py` | 若仍断言旧 2～3 keys，改为 `SELF_SERVICE_KEYS` |

---

### Task 1: 借入 Key Intent + commands 匹配

**Files:**
- Modify: `assistant_platform/conversation/intents.py`
- Modify: `pulse/channels/commands.py`（`_handle_key_loan_commands` 与帮助文案）
- Modify: `tests/test_command_cutover.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_command_cutover.py` 的 parametrize 中增加（或新建独立测试）：

```python
@pytest.mark.parametrize(
    "text,key",
    [
        ("我借的key", "key.loan.self.read"),
        ("我借的 Key", "key.loan.self.read"),
        ("借入的key", "key.loan.self.read"),
        ("借用状态", "key.loan.self.read"),
        ("借key", "key.loan.request"),
        ("借 Key 不够用了", "key.loan.request"),
    ],
)
def test_loan_intent_borrowed_vs_request(text, key):
    intent = match_capability_intent(text)
    assert intent is not None
    assert intent.capability_key == key


def test_lent_out_phrase_not_self_read():
    intent = match_capability_intent("我借出的key")
    assert intent is None or intent.capability_key != "key.loan.self.read"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_command_cutover.py::test_loan_intent_borrowed_vs_request tests/test_command_cutover.py::test_lent_out_phrase_not_self_read -v
```

Expected: FAIL（`我借的key` → `None`）

- [ ] **Step 3: 实现 intent 匹配**

在 `intents.py` 中，把精确匹配换成 helper，并放在 `_looks_like_borrow_key_command` **之前**：

```python
def _looks_like_self_loan_read(text: str) -> bool:
    t = text.strip().lower().replace(" ", "")
    if "借出" in t:
        return False
    if text.strip() in ("我的借用", "借用状态"):
        return True
    needles = ("我借的key", "我借的", "借入的key", "借入的", "借的key")
    return any(n in t for n in needles)


# 在 match_capability_intent 内：
if _looks_like_self_loan_read(stripped):
    return CapabilityIntent("key.loan.self.read", {"text": stripped})
```

注意：`"借的key"` 可能误伤「申请借的key」类长句——若同时 `_looks_like_borrow_key_command` 为真，**优先 self.read 仅当**含「我借的/借入/我的借用/借用状态」；将 `needles` 收紧为：

```python
needles = ("我借的key", "我借的", "借入的key", "借入的")
# 另：规范化后等于「借的key」也算 self.read
if t in ("借的key", "借的"):
    return True
```

- [ ] **Step 4: 同步 `commands.py`**

`_handle_key_loan_commands` 开头改为：

```python
from assistant_platform.conversation.intents import _looks_like_self_loan_read
# 或把 helper 放到 pulse/channels/loan_phrases.py 避免循环 import
# 推荐：把 _looks_like_self_loan_read 放到 pulse/channels/commands.py，intents 从 commands 导入

if _looks_like_self_loan_read(text):
    # 原「我的借用」分支逻辑不变
    ...
```

帮助文案增加一行：`· 我借的key / 我的借用 — 查看当前借用状态`

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_command_cutover.py -v -k "loan or match_capability"
```

Expected: PASS

- [ ] **Step 6: Commit（仅当用户要求时）**

```bash
git add assistant_platform/conversation/intents.py pulse/channels/commands.py tests/test_command_cutover.py
git commit -m "fix: match borrowed-key phrases to key.loan.self.read"
```

---

### Task 2: 用量周期解析与分账号聚合（纯函数）

**Files:**
- Create: `pulse/tool_center/usage_self.py`
- Create: `tests/test_usage_self.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_usage_self.py
from datetime import date

from pulse.tool_center.usage_self import (
    parse_usage_period_request,
    resolve_account_window,
    format_usage_self_message,
    aggregate_models_from_daily_rows,
)


def test_parse_default_billing_cycle():
    mode, period = parse_usage_period_request("查下我的用量", default_period="2026-07")
    assert mode == "billing_cycle"
    assert period == "2026-07"


def test_parse_calendar_month_keyword():
    mode, period = parse_usage_period_request("查下我的用量 自然月", default_period="2026-07")
    assert mode == "calendar_month"
    assert period == "2026-07"


def test_parse_explicit_yyyy_mm():
    mode, period = parse_usage_period_request("我的用量 2026-06", default_period="2026-07")
    assert mode == "calendar_month"
    assert period == "2026-06"


def test_resolve_billing_cycle_per_account():
    start, end, label = resolve_account_window(
        mode="billing_cycle",
        period="2026-07",
        usage_resets_on=date(2026, 7, 15),
        today=date(2026, 7, 10),
    )
    assert start == date(2026, 6, 15)
    assert end == date(2026, 7, 15)
    assert "记账" in label or "billing" in label.lower() or label.startswith("周期")


def test_resolve_fallback_without_resets_on():
    start, end, label = resolve_account_window(
        mode="billing_cycle",
        period="2026-07",
        usage_resets_on=None,
        today=date(2026, 7, 10),
    )
    assert start == date(2026, 7, 1)
    assert end == date(2026, 8, 1)
    assert "自然月" in label


def test_format_lists_all_models():
    msg = format_usage_self_message(
        mode="billing_cycle",
        period="2026-07",
        accounts=[
            {
                "identifier": "a@x.com",
                "window_label": "记账周期",
                "range_text": "2026-06-15 ~ 2026-07-14",
                "events": 10,
                "tokens": 100,
                "cost_usd": 1.5,
                "models": [
                    {"model": "m1", "events": 6, "tokens": 60, "cost_usd": 1.0},
                    {"model": "m2", "events": 4, "tokens": 40, "cost_usd": 0.5},
                ],
            }
        ],
    )
    assert "a@x.com" in msg
    assert "m1" in msg and "m2" in msg
    assert "记账周期" in msg or "用量" in msg
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_usage_self.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `pulse/tool_center/usage_self.py`**

核心 API（实现时保持这些名字）：

```python
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.periods import current_period
from pulse.storage.models import UsageDailyAggregate, UsageIngestion, UsageRecord
from pulse.tool_center.billing_cycle import (
    billing_cycle_containing,
    format_cycle_range,
    period_first_day,
    add_months,
)

_PERIOD_RE = re.compile(r"(20\d{2})-(0[1-9]|1[0-2])")


def parse_usage_period_request(text: str, *, default_period: str) -> tuple[str, str]:
    """返回 (mode, period)。mode: billing_cycle | calendar_month。"""
    m = _PERIOD_RE.search(text or "")
    if m:
        return "calendar_month", m.group(0)
    if any(k in (text or "") for k in ("自然月", "本月")):
        return "calendar_month", default_period
    return "billing_cycle", default_period


def is_self_usage_query(text: str) -> bool:
    t = (text or "").strip()
    if "用量" not in t:
        return False
    # 团队 NL 排除：谁/排名/最多（无「我」时）
    if any(k in t for k in ("谁", "排名", "最多")) and not any(k in t for k in ("我", "本人")):
        return False
    return any(k in t for k in ("我", "本人")) or t in ("用量",) or t.endswith("用量")


def resolve_account_window(
    *,
    mode: str,
    period: str,
    usage_resets_on: date | None,
    today: date,
) -> tuple[date, date, str]:
    """返回 [start, end) 与窗口说明标签。"""
    if mode == "calendar_month" or not usage_resets_on:
        start = period_first_day(period)
        end = add_months(start, 1)
        label = f"自然月 {period}" if mode == "calendar_month" or not usage_resets_on else f"自然月 {period}"
        if mode == "billing_cycle" and not usage_resets_on:
            label = f"自然月 {period}（无记账重置日，已回退）"
        return start, end, label
    start, end = billing_cycle_containing(today, usage_resets_on)
    return start, end, "记账周期"


def aggregate_models_from_daily_rows(rows: list[UsageDailyAggregate]) -> list[dict[str, Any]]:
    by_model: dict[str, dict[str, Any]] = {}
    for r in rows:
        bucket = by_model.setdefault(
            r.model,
            {"model": r.model, "events": 0, "tokens": 0, "cost_usd": 0.0},
        )
        bucket["events"] += int(r.event_count or 0)
        bucket["tokens"] += int(r.tokens_input or 0) + int(r.tokens_output or 0) + int(r.tokens_cache_read or 0)
        bucket["cost_usd"] += float(r.total_cost_usd or 0)
    return sorted(by_model.values(), key=lambda x: (-x["cost_usd"], -x["events"], x["model"]))


def load_account_model_usage(
    session: Session,
    *,
    account_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(UsageDailyAggregate).where(
                UsageDailyAggregate.account_id == account_id,
                UsageDailyAggregate.event_date >= start,
                UsageDailyAggregate.event_date < end,
            )
        ).all()
    )
    if rows:
        return aggregate_models_from_daily_rows(rows)
    # 回退 UsageRecord
    recs = session.execute(
        select(UsageRecord)
        .join(UsageIngestion, UsageRecord.ingestion_id == UsageIngestion.id)
        .where(
            UsageIngestion.account_id == account_id,
            UsageIngestion.status == "confirmed",
            UsageRecord.event_date >= start,
            UsageRecord.event_date < end,
        )
    ).scalars().all()
    by_model: dict[str, dict[str, Any]] = {}
    for r in recs:
        bucket = by_model.setdefault(
            r.model,
            {"model": r.model, "events": 0, "tokens": 0, "cost_usd": 0.0},
        )
        bucket["events"] += 1
        bucket["tokens"] += int(r.tokens_total or 0)
        bucket["cost_usd"] += float(r.cost_usd or 0)
    return sorted(by_model.values(), key=lambda x: (-x["cost_usd"], -x["events"], x["model"]))


def format_usage_self_message(
    *,
    mode: str,
    period: str,
    accounts: list[dict[str, Any]],
) -> str:
    if not accounts:
        return "尚未绑定 Cursor 账号"
    header = (
        f"你的用量（自然月 {period}，分账号）："
        if mode == "calendar_month"
        else "你的用量（记账周期，分账号）："
    )
    lines = [header, ""]
    for acc in accounts:
        lines.append(f"· {acc['identifier']}")
        lines.append(f"  周期：{acc['range_text']}（{acc['window_label']}）")
        lines.append(
            f"  合计：{acc['events']:,} 次，{acc['tokens']:,} tokens，付费 ${acc['cost_usd']:.2f}"
        )
        models = acc.get("models") or []
        if models:
            lines.append("  模型：")
            for m in models:
                lines.append(
                    f"  - {m['model']}：{m['events']:,} 次，{m['tokens']:,} tokens，${m['cost_usd']:.2f}"
                )
        else:
            lines.append("  模型：暂无已上报明细")
        lines.append("")
    lines.append("也可发送「额度」查看 Cursor 额度快照。")
    return "\n".join(lines).rstrip()


def build_usage_self_reply(
    session: Session,
    *,
    accounts: list[Any],  # AiAccount
    text: str,
    config: Any,
    today: date | None = None,
) -> str:
    from pulse.tool_center.account_pick import filter_cursor_accounts

    today = today or date.today()
    default_period = current_period(config)
    mode, period = parse_usage_period_request(text, default_period=default_period)
    cursor_accounts = filter_cursor_accounts(accounts)
    if not cursor_accounts:
        return "尚未绑定 Cursor 账号"
    payload = []
    for account in cursor_accounts:
        start, end, label = resolve_account_window(
            mode=mode,
            period=period,
            usage_resets_on=account.usage_resets_on,
            today=today,
        )
        models = load_account_model_usage(
            session, account_id=account.id, start=start, end=end
        )
        payload.append(
            {
                "identifier": account.account_identifier,
                "window_label": label,
                "range_text": format_cycle_range(start, end),
                "events": sum(m["events"] for m in models),
                "tokens": sum(m["tokens"] for m in models),
                "cost_usd": sum(m["cost_usd"] for m in models),
                "models": models,
            }
        )
    return format_usage_self_message(mode=mode, period=period, accounts=payload)
```

按测试微调 `resolve_account_window` 的 label 断言（测试里用 `"自然月" in label` / 记账相关即可）。

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_usage_self.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add pulse/tool_center/usage_self.py tests/test_usage_self.py
git commit -m "feat: add per-account self usage aggregation and formatting"
```

---

### Task 3: 接入 `usage.query` handler 与 `run_command`

**Files:**
- Create: `pulse/capabilities/handlers/usage_query.py`
- Create: `tests/test_capability_usage_query.py`
- Modify: `pulse/capabilities/invoke.py`
- Modify: `pulse/channels/commands.py`
- Modify: `assistant_platform/conversation/intents.py`（可选：arguments 写入 period_mode）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_capability_usage_query.py
from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.usage_query import handle_usage_query
from pulse.capabilities.invoke import HANDLERS
from pulse.config import AppConfig, CollectionConfig, CredentialConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_usage_query_registered():
    assert ("usage.query", "1") in HANDLERS


def test_handle_self_usage_no_accounts():
    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    actor = repo.add_member("actor-user", "Actor")
    repo.commit()
    config = AppConfig(
        credentials=CredentialConfig(encryption_key=""),
        collection=CollectionConfig(timezone="Asia/Shanghai", period_format="%Y-%m"),
    )
    req = CapabilityInvokeRequest(
        invocation_id="inv-u1",
        idempotency_key="idem-u1",
        team_id=team.id,
        actor_member_id=actor.id,
        capability_key="usage.query",
        capability_version="1",
        arguments={"text": "查下我的用量"},
        confirmed=True,
    )
    result = handle_usage_query(session, request=req, config=config, op={})
    assert result.status == "succeeded"
    assert "尚未绑定" in result.user_message
```

另在 `tests/test_command_cutover.py`：

```python
def test_intent_usage_carries_text():
    intent = match_capability_intent("查下我的用量 自然月")
    assert intent.capability_key == "usage.query"
    assert "自然月" in intent.arguments["text"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_capability_usage_query.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 handler 并注册**

```python
# pulse/capabilities/handlers/usage_query.py
from __future__ import annotations
from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.capabilities.handlers.channel_command import handle_channel_command, resolve_actor_member
from pulse.storage.models import Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.usage_self import build_usage_self_reply, is_self_usage_query


def handle_usage_query(session, *, request, config, op):
    text = str(request.arguments.get("text") or "").strip()
    if text and is_self_usage_query(text):
        member = resolve_actor_member(session, request)
        if member is None:
            return CapabilityInvokeResult(
                status="failed",
                error_code="forbidden",
                user_message="成员不存在或无权访问",
            )
        tool_repo = ToolCenterRepository(session, request.team_id)
        accounts = tool_repo.get_primary_accounts_for_member(member.id)
        reply = build_usage_self_reply(
            session, accounts=accounts, text=text, config=config
        )
        return CapabilityInvokeResult(
            status="succeeded",
            user_message=reply,
            result={"mode": "self"},
        )
    return handle_channel_command(session, request=request, config=config, op=op)
```

`invoke.py`：

```python
from pulse.capabilities.handlers.usage_query import handle_usage_query

_SPECIAL = {
    ...
    ("usage.query", "1"): handle_usage_query,
}
```

`commands.py` 在 `if looks_like_query(text):` **之前**：

```python
from pulse.tool_center.usage_self import is_self_usage_query, build_usage_self_reply

if is_self_usage_query(text):
    member = repo.get_member_by_dingtalk_id(user_id)
    if not member:
        return "未找到你的成员记录。"
    from pulse.tool_center.repository import ToolCenterRepository
    tool_repo = ToolCenterRepository(repo.session, repo.team_id)
    return build_usage_self_reply(
        repo.session,
        accounts=tool_repo.get_primary_accounts_for_member(member.id),
        text=text,
        config=config,
    )
```

「查询 我的用量」分支：`question` 取出后若 `is_self_usage_query(question)` 同样走 `build_usage_self_reply`。

- [ ] **Step 4: 跑测试**

```bash
pytest tests/test_capability_usage_query.py tests/test_usage_self.py tests/test_command_cutover.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add pulse/capabilities/handlers/usage_query.py pulse/capabilities/invoke.py pulse/channels/commands.py tests/test_capability_usage_query.py
git commit -m "feat: route self usage queries to per-account billing-cycle reply"
```

---

### Task 4: Seed 去重重复 pack/assignment

**Files:**
- Modify: `assistant_platform/capabilities/seed.py`
- Modify: `tests/assistant_platform/test_capability_registry_seed.py`
- Modify: `tests/assistant_platform/test_capability_resolve.py`（若仍断言旧 keys）

- [ ] **Step 1: 写失败测试**

在 `test_capability_registry_seed.py`：

```python
from assistant_platform.capabilities.catalog import (
    CAPABILITY_OPERATIONS,
    SELF_SERVICE_KEYS,
    OWNER_EXTRA_KEYS,
)
from assistant_platform.capabilities.models import (
    CapabilityAssignmentRow,
    CapabilityPackItemRow,
    CapabilityPackRow,
)
from assistant_platform.capabilities.seed import seed_phase1_capabilities


def test_seed_dedupes_duplicate_packs():
    Session = init_assistant_db("sqlite://", team_id="team-dup")
    session = Session()
    # 手工插入重复旧包（少 items）
    old = CapabilityPackRow(
        team_id="team-dup", key="cursor_self_service", display_name="old"
    )
    session.add(old)
    session.flush()
    session.add(
        CapabilityAssignmentRow(
            team_id="team-dup",
            scope_type="team_default",
            scope_id="",
            pack_id=old.id,
        )
    )
    session.commit()

    seed_phase1_capabilities(session, "team-dup")
    session.commit()

    packs = session.scalars(
        select(CapabilityPackRow).where(
            CapabilityPackRow.team_id == "team-dup",
            CapabilityPackRow.key == "cursor_self_service",
        )
    ).all()
    assert len(packs) == 1
    items = {
        i.capability_key
        for i in session.scalars(
            select(CapabilityPackItemRow).where(CapabilityPackItemRow.pack_id == packs[0].id)
        ).all()
    }
    assert set(SELF_SERVICE_KEYS) <= items

    assigns = session.scalars(
        select(CapabilityAssignmentRow).where(
            CapabilityAssignmentRow.team_id == "team-dup",
            CapabilityAssignmentRow.scope_type == "team_default",
        )
    ).all()
    assert len(assigns) == 1
    assert assigns[0].pack_id == packs[0].id


def test_seed_counts_match_catalog():
    Session = init_assistant_db("sqlite://", team_id="team-full")
    session = Session()
    assert session.scalar(select(func.count()).select_from(CapabilityDefinitionRow)) == len(
        CAPABILITY_OPERATIONS
    )
    assert session.scalar(select(func.count()).select_from(CapabilityPackRow)) == 2
    assert session.scalar(select(func.count()).select_from(CapabilityAssignmentRow)) == 3
```

把旧的 `== 3` definitions / `assignments == 2` 断言全部改掉（operator 也有 assignment → **3**）。

`test_packs_contain_expected_keys` 改为断言 `SELF_SERVICE_KEYS` / owner 全集。

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/assistant_platform/test_capability_registry_seed.py -v
```

Expected: 旧断言 FAIL 或 dedupe 未实现 FAIL

- [ ] **Step 3: 实现 `_dedupe_packs_for_team`**

在 `seed.py`，`seed_phase1_capabilities` **开头**（创建定义之后、或 get_or_create pack 之前）调用：

```python
def _dedupe_packs_for_team(session: Session, team_id: str, pack_keys: list[str]) -> None:
    for key in pack_keys:
        packs = list(
            session.scalars(
                select(CapabilityPackRow).where(
                    CapabilityPackRow.team_id == team_id,
                    CapabilityPackRow.key == key,
                )
            ).all()
        )
        if len(packs) <= 1:
            continue
        # 选 item 最多者为规范包；并列取 created_at 最早
        def score(p: CapabilityPackRow) -> tuple[int, str]:
            n = session.scalar(
                select(func.count()).select_from(CapabilityPackItemRow).where(
                    CapabilityPackItemRow.pack_id == p.id
                )
            ) or 0
            return (int(n), p.created_at.isoformat() if p.created_at else "")

        canonical = max(packs, key=score)
        for p in packs:
            if p.id == canonical.id:
                continue
            # 迁移 assignment
            for a in session.scalars(
                select(CapabilityAssignmentRow).where(CapabilityAssignmentRow.pack_id == p.id)
            ).all():
                # 若同 scope 已有指向 canonical 的 assignment，删重复
                existing = session.scalar(
                    select(CapabilityAssignmentRow).where(
                        CapabilityAssignmentRow.team_id == team_id,
                        CapabilityAssignmentRow.scope_type == a.scope_type,
                        CapabilityAssignmentRow.scope_id == a.scope_id,
                        CapabilityAssignmentRow.pack_id == canonical.id,
                    )
                )
                if existing is not None:
                    session.delete(a)
                else:
                    a.pack_id = canonical.id
            # 删 items + pack
            for item in session.scalars(
                select(CapabilityPackItemRow).where(CapabilityPackItemRow.pack_id == p.id)
            ).all():
                session.delete(item)
            session.delete(p)
        session.flush()

    # 同一 (scope_type, scope_id) 多条 pack assignment：只留一条
    scopes = session.execute(
        select(
            CapabilityAssignmentRow.scope_type,
            CapabilityAssignmentRow.scope_id,
        ).where(
            CapabilityAssignmentRow.team_id == team_id,
            CapabilityAssignmentRow.pack_id.is_not(None),
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    for scope_type, scope_id in scopes:
        key = (scope_type, scope_id or "")
        if key in seen:
            continue
        seen.add(key)
        rows = list(
            session.scalars(
                select(CapabilityAssignmentRow).where(
                    CapabilityAssignmentRow.team_id == team_id,
                    CapabilityAssignmentRow.scope_type == scope_type,
                    CapabilityAssignmentRow.scope_id == (scope_id or ""),
                    CapabilityAssignmentRow.pack_id.is_not(None),
                )
            ).all()
        )
        if len(rows) <= 1:
            continue
        keep = rows[0]
        for extra in rows[1:]:
            session.delete(extra)
    session.flush()
```

然后在 `seed_phase1_capabilities`：

```python
def seed_phase1_capabilities(session: Session, team_id: str) -> None:
    for spec in _definitions_from_catalog():
        definition = _get_or_create_definition(session, spec)
        _get_or_create_version(session, definition, spec)

    _dedupe_packs_for_team(
        session, team_id, [p["key"] for p in _PHASE1_PACKS]
    )
    # 现有 get_or_create pack/item/assignment 逻辑保持不变
```

另需：对同一 `scope_type+scope_id` 若仍有多条（指向不同已合并前的 id），合并后只留一条。

- [ ] **Step 4: 同步 resolve 测试**

打开 `tests/assistant_platform/test_capability_resolve.py`，凡 `== {"cursor.key.bind", "quota.self.read"}` 改为 `set(SELF_SERVICE_KEYS)`（或 `>=` 子集断言）。

- [ ] **Step 5: 跑测试**

```bash
pytest tests/assistant_platform/test_capability_registry_seed.py tests/assistant_platform/test_capability_resolve.py tests/assistant_platform/test_capability_api.py -v
```

Expected: PASS

- [ ] **Step 6: 本地脏库（可选手动）**

重启 Assistant 或调用一次 `seed_phase1_capabilities` 后，确认 `data/assistant.db` 中每个 pack key 仅一行。

- [ ] **Step 7: Commit（仅当用户要求时）**

```bash
git add assistant_platform/capabilities/seed.py tests/assistant_platform/
git commit -m "fix: dedupe capability packs on seed and align catalog assertions"
```

---

### Task 5: 回归与文档收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-07-15-usage-self-loan-capability-cleanup-design.md`（状态 → 已批准/实施中）
- Modify: `pulse/channels/commands.py` 帮助文案（若 Task 1 未改完）

- [x] **Step 1: 跑相关回归**

```bash
pytest tests/test_command_cutover.py tests/test_usage_self.py tests/test_capability_usage_query.py tests/test_capability_quota_self_read.py tests/test_key_loan_dingtalk.py tests/assistant_platform/test_capability_registry_seed.py tests/assistant_platform/test_capability_resolve.py -v
```

Expected: PASS

- [x] **Step 2: 更新 spec 状态为「已批准并实施」**

- [ ] **Step 3: Commit（仅当用户要求时）**

---

## Spec 覆盖自检

| Spec 要求 | Task |
|-----------|------|
| 能力 Key=`—` 属 pack 分配（文档说明） | 已在 spec；清理在 Task 4 |
| 分账号用量 | Task 2–3 |
| 全部模型明细 | Task 2 `format_usage_self_message` |
| 默认记账周期、可切自然月 | Task 2 `parse_usage_period_request` |
| 无 resets_on 回退 | Task 2 `resolve_account_window` |
| 我借的key → self.read | Task 1 |
| 不改团队 NL | Task 3 非自助仍委托 channel_command |
| Seed 去重 | Task 4 |

## 执行说明

- 提交步骤默认**等用户明确要求再 commit**（与仓库用户规则一致）。
- 实施顺序必须 Task 1 → 2 → 3 → 4 → 5；Task 2 可与 Task 1 并行，但 Task 3 依赖 Task 2。
