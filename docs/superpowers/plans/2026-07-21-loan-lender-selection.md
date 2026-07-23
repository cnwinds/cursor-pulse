# 借用账号选择算法优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 出借账号选择算法改为"临期优先消化"（deadline 驱动 urgency 打分），单账号同时最多借给 2 人且并发安全，key 按 deadline 自动回收并展示回收日。

**Architecture:** 规格见 `docs/superpowers/specs/2026-07-21-loan-lender-selection-design.md`。`burn_rate.py` 保持纯函数：新增 `LenderCandidate` 与 deadline/surplus/urgency/freshness 计算，重写 `recommend_lenders`；DB 组装集中在 `key_loans.py` 的 `build_lender_candidates`；人数上限在 `issue_loan_key` 事务内复查（唯一发放漏斗），sqlite 用写锁、Postgres 用行锁串行化；回收任务 `expire_loans_on_reset` 判定日扩展为 `min(usage_resets_on, renews_on)`。

**Tech Stack:** Python 3.12、SQLAlchemy 2（sqlite/Postgres）、pydantic v2 配置、FastAPI 管理端、pytest。

**测试命令约定：** 一律用 `.venv/Scripts/python -m pytest <args>`（仓库根目录下执行）。

**规格与实现的一处细化差异（已反映回本计划末尾的规格修订步骤）：** U、S 两个额度因子在候选池内 min-max 归一；L（在借人数）、F（快照新鲜度）本身即 [0,1] 绝对值直接入分——对 F 做 min-max 会把快照时间的微差噪声放大成满分差距，破坏确定性。

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `pulse/config.py` | `LoanSelectionConfig` / `ToolCenterConfig` 配置模型 | Modify |
| `config.example.yaml` | `tool_center.loan_selection` 配置块 | Modify |
| `pulse/tool_center/burn_rate.py` | 候选 dataclass、deadline/surplus/freshness、打分排序（纯函数） | Modify |
| `pulse/tool_center/key_loans.py` | 候选组装、推荐、发放上限+锁、按 deadline 回收、payload | Modify |
| `pulse/tool_center/key_loan_ops.py` | 配置透传、bot 文案展示回收日 | Modify |
| `pulse/web/quota_api.py` | `/recommend` 与 `/accounts/{id}/loan-key` 路由接线 | Modify |
| `tests/test_burn_rate.py` | 打分单测（重写推荐部分） | Modify |
| `tests/test_loan_selection_config.py` | 配置默认/覆盖 | Create |
| `tests/test_lender_selection.py` | 候选组装、推荐排除、回收、payload/文案 | Create |
| `tests/test_key_loan_caps.py` | 发放上限（服务层） | Create |
| `tests/test_quota_api.py` | 上限路由测试（追加） | Modify |
| `docs/superpowers/specs/2026-07-21-loan-lender-selection-design.md` | §3 归一化措辞 + 状态 | Modify |

---

### Task 1: 配置 `LoanSelectionConfig`

**Files:**
- Modify: `pulse/config.py`（在 `CursorSyncConfig` 之后、`AppConfig` 之前插入；`AppConfig` 增加字段）
- Modify: `config.example.yaml`（文件末尾追加）
- Test: `tests/test_loan_selection_config.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_loan_selection_config.py`：

```python
from __future__ import annotations

from pulse.config import AppConfig


def test_loan_selection_defaults():
    cfg = AppConfig()
    sel = cfg.tool_center.loan_selection
    assert sel.max_active_loans_per_account == 2
    assert sel.min_coverage_days == 1
    assert sel.freshness_full_penalty_hours == 24.0
    assert sel.weight_urgency == 0.50
    assert sel.weight_surplus == 0.25
    assert sel.weight_load == 0.15
    assert sel.weight_freshness == 0.10


def test_loan_selection_yaml_override():
    cfg = AppConfig.model_validate(
        {
            "tool_center": {
                "loan_selection": {
                    "max_active_loans_per_account": 3,
                    "weight_urgency": 0.7,
                }
            }
        }
    )
    sel = cfg.tool_center.loan_selection
    assert sel.max_active_loans_per_account == 3
    assert sel.weight_urgency == 0.7
    # 未覆盖的键保持默认
    assert sel.weight_surplus == 0.25
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_loan_selection_config.py -v`
Expected: FAIL（`AppConfig` 无 `tool_center` 属性，AttributeError）

- [x] **Step 3: 实现配置**

在 `pulse/config.py` 的 `CursorSyncConfig` 类定义结束之后（`EVOLUTION_DAY_DAILY` 常量之前）插入：

```python
class LoanSelectionConfig(BaseModel):
    """Key 借用出借账号选择参数（打分权重与硬上限）。"""

    max_active_loans_per_account: int = 2
    min_coverage_days: int = 1
    freshness_full_penalty_hours: float = 24.0
    weight_urgency: float = 0.50
    weight_surplus: float = 0.25
    weight_load: float = 0.15
    weight_freshness: float = 0.10


class ToolCenterConfig(BaseModel):
    loan_selection: LoanSelectionConfig = Field(default_factory=LoanSelectionConfig)
```

在 `AppConfig` 中（`cursor_sync` 字段之后）增加：

```python
    tool_center: ToolCenterConfig = Field(default_factory=ToolCenterConfig)
```

在 `config.example.yaml` 末尾追加：

```yaml

tool_center:
  loan_selection:
    max_active_loans_per_account: 2   # 单账号最多同时借给几人
    min_coverage_days: 1              # 距额度作废不足该天数的账号不外借
    freshness_full_penalty_hours: 24  # 快照新鲜度惩罚尺度（小时），超过则新鲜度因子为 0
    weight_urgency: 0.50              # 日均待消化额度（临期+富余）权重
    weight_surplus: 0.25              # 富余额度绝对值权重
    weight_load: 0.15                 # 在借人数（越少越好）权重
    weight_freshness: 0.10            # 快照新鲜度权重
```

- [x] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_loan_selection_config.py -v`
Expected: 2 passed

- [x] **Step 5: 提交**

```bash
git add pulse/config.py config.example.yaml tests/test_loan_selection_config.py
git commit -m "feat(config): add tool_center.loan_selection settings for lender selection"
```

---

### Task 2: 打分重写 + 选号链路切换（burn_rate / key_loans / quota_api recommend）

本任务把新打分算法上线到所有选号入口，保证提交时全仓测试绿。

**Files:**
- Modify: `pulse/tool_center/burn_rate.py`（删除 `_lender_score`，重写 `recommend_lenders`）
- Modify: `pulse/tool_center/key_loans.py:273-293`（`recommend_lender_for_borrower` 重写；新增 `build_lender_candidates` 等）
- Modify: `pulse/web/quota_api.py:154-166`（`quota_recommend` 走新链路）
- Test: `tests/test_burn_rate.py`（重写推荐测试）
- Test: `tests/test_lender_selection.py`（新建，DB 级）

- [x] **Step 1: 写失败测试 — test_burn_rate.py 全量替换**

把 `tests/test_burn_rate.py` 整体替换为：

```python
from __future__ import annotations

from datetime import date, datetime, timezone

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.burn_rate import (
    LenderCandidate,
    analyze_burn_rate,
    projected_surplus_cents,
    quota_progress,
    recommend_lenders,
)

TODAY = date(2026, 7, 10)


def _snapshot(
    *,
    cycle_start: date,
    cycle_end: date,
    account_id: str = "acc-1",
    limit_cents: int = 7000,
    used_cents: int = 0,
    remaining_cents: int = 7000,
    total_pct: float | None = None,
    auto_pct: float | None = None,
    api_pct: float | None = None,
) -> AccountQuotaSnapshot:
    return AccountQuotaSnapshot(
        account_id=account_id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        limit_cents=limit_cents,
        used_cents=used_cents,
        remaining_cents=remaining_cents,
        total_pct=total_pct,
        auto_pct=auto_pct,
        api_pct=api_pct,
    )


def _candidate(
    snap: AccountQuotaSnapshot,
    *,
    account_id: str,
    identifier: str | None = None,
    renews_on: date | None = None,
    active_loans: int = 0,
) -> LenderCandidate:
    return LenderCandidate(
        snapshot=snap,
        account_id=account_id,
        account_identifier=identifier or f"{account_id}@x.com",
        renews_on=renews_on,
        active_loans=active_loans,
    )


def test_healthy_burn_rate_uses_cursor_total_pct():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        used_cents=15605,
        remaining_cents=0,
        total_pct=24.0,
        auto_pct=27.0,
        api_pct=10.0,
    )
    analysis = analyze_burn_rate(snap, today=date(2026, 7, 13))
    assert analysis.status == "healthy"
    assert quota_progress(snap) == 0.24
    assert analysis.remaining_headroom_pct == 76.0


def test_exhausted_when_total_pct_reaches_100():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        total_pct=100.0,
    )
    analysis = analyze_burn_rate(snap, today=date(2026, 7, 20))
    assert analysis.status == "exhausted"


def test_warning_when_total_pct_high_or_exhausts_before_reset():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        total_pct=70.0,
        auto_pct=50.0,
        api_pct=40.0,
    )
    analysis = analyze_burn_rate(snap, today=date(2026, 7, 5))
    assert analysis.status == "warning"
    assert analysis.exhausts_before_reset is True


def test_recommend_lenders_skips_exhausted_by_total_pct():
    healthy = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a1",
        total_pct=20.0,
        used_cents=1400,
        remaining_cents=5600,
    )
    exhausted = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a2",
        total_pct=100.0,
        used_cents=7000,
        remaining_cents=0,
    )
    ranked = recommend_lenders(
        [
            _candidate(exhausted, account_id="a2"),
            _candidate(healthy, account_id="a1"),
        ],
        today=date(2026, 7, 10),
    )
    assert len(ranked) == 1
    assert ranked[0]["account_id"] == "a1"
    assert ranked[0]["remaining_headroom_pct"] == 80.0


def test_recommend_prefers_near_deadline_account_for_digestion():
    far = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="far",
        limit_cents=20000,
        used_cents=2000,
        remaining_cents=18000,
    )
    near = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 13),
        account_id="near",
        limit_cents=7000,
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [
            _candidate(far, account_id="far"),
            _candidate(near, account_id="near"),
        ],
        today=TODAY,
    )
    assert [r["account_id"] for r in ranked] == ["near", "far"]
    assert ranked[0]["days_to_deadline"] == 3


def test_recommend_filters_accounts_at_loan_cap():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="full",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [_candidate(snap, account_id="full", active_loans=2)],
        today=TODAY,
    )
    assert ranked == []


def test_recommend_penalizes_but_allows_partially_loaded_account():
    loaded = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="loaded",
        used_cents=1000,
        remaining_cents=6000,
    )
    idle = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="idle",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [
            _candidate(loaded, account_id="loaded", active_loans=1),
            _candidate(idle, account_id="idle", active_loans=0),
        ],
        today=TODAY,
    )
    assert [r["account_id"] for r in ranked] == ["idle", "loaded"]


def test_recommend_filters_when_deadline_too_close():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=TODAY,
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders([_candidate(snap, account_id="a1")], today=TODAY)
    assert ranked == []


def test_renews_on_overrides_cycle_end_as_deadline():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [_candidate(snap, account_id="a1", renews_on=date(2026, 7, 12))],
        today=TODAY,
    )
    assert ranked[0]["days_to_deadline"] == 2
    assert ranked[0]["deadline"] == "2026-07-12"


def test_renews_on_in_past_filters_account():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders(
        [_candidate(snap, account_id="a1", renews_on=date(2026, 7, 9))],
        today=TODAY,
    )
    assert ranked == []


def test_surplus_excludes_owner_future_consumption():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        limit_cents=7000,
        used_cents=900,
        remaining_cents=6100,
    )
    # 号主日均 100 cents，30 天还要用 3000 → 富余 3100
    assert projected_surplus_cents(snap, 30, today=TODAY) == 3100.0


def test_single_candidate_scores_sum_of_weights():
    snap = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="a1",
        used_cents=1000,
        remaining_cents=6000,
    )
    ranked = recommend_lenders([_candidate(snap, account_id="a1")], today=TODAY)
    assert ranked[0]["score"] == 1.0


def test_weight_override_shifts_priority_to_surplus():
    far = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 9),
        account_id="far",
        limit_cents=20000,
        used_cents=2000,
        remaining_cents=18000,
    )
    near = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 7, 13),
        account_id="near",
        limit_cents=7000,
        used_cents=1000,
        remaining_cents=6000,
    )
    selection = LoanSelectionConfig(
        weight_urgency=0.0,
        weight_surplus=1.0,
        weight_load=0.0,
        weight_freshness=0.0,
    )
    ranked = recommend_lenders(
        [_candidate(near, account_id="near"), _candidate(far, account_id="far")],
        today=TODAY,
        loan_selection=selection,
    )
    assert ranked[0]["account_id"] == "far"


def test_tie_break_by_account_id():
    fixed = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    snap_b = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="b",
        used_cents=1000,
        remaining_cents=6000,
    )
    snap_a = _snapshot(
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        account_id="a",
        used_cents=1000,
        remaining_cents=6000,
    )
    snap_b.captured_at = fixed
    snap_a.captured_at = fixed
    ranked = recommend_lenders(
        [_candidate(snap_b, account_id="b"), _candidate(snap_a, account_id="a")],
        today=TODAY,
        now=fixed,
    )
    assert [r["account_id"] for r in ranked] == ["a", "b"]
```

- [x] **Step 2: 写失败测试 — 新建 tests/test_lender_selection.py（本任务先放候选组装与推荐用例）**

```python
from __future__ import annotations

import base64
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from pulse.storage.db import init_db
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccountCredential,
    KeyLoan,
)
from pulse.tool_center.key_loans import (
    build_lender_candidates,
    recommend_lender_for_borrower,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def lender_env():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    member = repo.add_member("sel-member", "SelMember")
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    today = date.today()
    for acc in accounts:
        session.add(
            AccountQuotaSnapshot(
                account_id=acc.id,
                captured_at=datetime.now(timezone.utc),
                cycle_start=today - timedelta(days=10),
                cycle_end=today + timedelta(days=20),
                limit_cents=7000,
                used_cents=1000,
                remaining_cents=6000,
                total_pct=14.0,
            )
        )
    repo.commit()
    yield {
        "session": session,
        "repo": repo,
        "tool_repo": tool_repo,
        "member": member,
        "accounts": accounts,
    }
    session.close()


def _make_loan(session, env, account, borrower) -> KeyLoan:
    cred = AiAccountCredential(
        account_id=account.id,
        vendor_id=account.vendor_id,
        credential_type="cursor_api_key",
        encrypted_value="enc",
        key_hint="hint",
        key_role="loan",
        bound_by_member_id=env["member"].id,
        assignee_member_id=borrower.id,
    )
    session.add(cred)
    session.flush()
    loan = KeyLoan(
        source_account_id=account.id,
        credential_id=cred.id,
        borrower_member_id=borrower.id,
        baseline_used_cents=0,
        status="active",
    )
    session.add(loan)
    session.flush()
    return loan


def test_build_candidates_counts_active_loans(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    b1 = env["repo"].add_member("cnt-b1", "CntB1")
    session.flush()
    _make_loan(session, env, account, b1)

    candidates = build_lender_candidates(session, env["repo"].team_id)
    by_id = {c.account_id: c for c in candidates}
    assert by_id[account.id].active_loans == 1
    assert all(
        c.active_loans == 0 for acc_id, c in by_id.items() if acc_id != account.id
    )


def test_recommend_excludes_account_at_cap(lender_env):
    env = lender_env
    session = env["session"]
    a = env["accounts"][0]
    b1 = env["repo"].add_member("rec-b1", "RecB1")
    b2 = env["repo"].add_member("rec-b2", "RecB2")
    session.flush()
    _make_loan(session, env, a, b1)
    _make_loan(session, env, a, b2)

    result = recommend_lender_for_borrower(session, env["repo"].team_id)
    assert result is not None
    assert result["account_id"] != a.id
    assert result["active_loans"] == 0


def test_recommend_respects_excluded_account_ids(lender_env):
    env = lender_env
    a = env["accounts"][0]
    result = recommend_lender_for_borrower(
        env["session"],
        env["repo"].team_id,
        exclude_account_ids={acc.id for acc in env["accounts"] if acc.id != a.id},
    )
    assert result is not None
    assert result["account_id"] == a.id


def test_recommend_returns_none_when_all_accounts_full(lender_env):
    env = lender_env
    session = env["session"]
    borrowers = [
        env["repo"].add_member(f"full-b{i}", f"FullB{i}")
        for i in range(len(env["accounts"]) * 2)
    ]
    session.flush()
    for idx, acc in enumerate(env["accounts"]):
        _make_loan(session, env, acc, borrowers[idx * 2])
        _make_loan(session, env, acc, borrowers[idx * 2 + 1])

    assert recommend_lender_for_borrower(session, env["repo"].team_id) is None
```

- [x] **Step 3: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_burn_rate.py tests/test_lender_selection.py -v`
Expected: FAIL（`cannot import name 'LenderCandidate' from pulse.tool_center.burn_rate`）

- [x] **Step 4: 重写 burn_rate.py 打分部分**

`pulse/tool_center/burn_rate.py`：头部 import 改为（新增 `datetime, timezone` 与配置）：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AccountQuotaSnapshot
```

（`timedelta` 已被 `projected_exhaustion_date` 使用，保留。）

保留文件开头到 `analyze_burn_rate` 结束（第 1-107 行现状）不变。**删除 `_lender_score` 与旧 `recommend_lenders`**（现第 110-153 行），替换为：

```python
@dataclass
class LenderCandidate:
    snapshot: AccountQuotaSnapshot
    account_id: str
    account_identifier: str
    renews_on: date | None = None
    active_loans: int = 0


def lender_deadline(cycle_end: date, renews_on: date | None) -> date:
    """额度作废截止日：账期重置日与订阅到期日取先到者。"""
    if renews_on and renews_on < cycle_end:
        return renews_on
    return cycle_end


def projected_surplus_cents(
    snapshot: AccountQuotaSnapshot, days_to_deadline: int, today: date | None = None
) -> float:
    """号主自身消耗到 deadline 也用不完的额度（cents）；无法推算时为 0。"""
    today = today or date.today()
    elapsed = max((today - snapshot.cycle_start).days, 1)
    if snapshot.remaining_cents > 0:
        daily_burn = snapshot.used_cents / elapsed
        return round(max(snapshot.remaining_cents - daily_burn * days_to_deadline, 0.0), 2)
    if snapshot.total_pct is not None and snapshot.limit_cents > 0:
        daily_pct = snapshot.total_pct / elapsed
        surplus_pct = max(100.0 - (snapshot.total_pct + daily_pct * days_to_deadline), 0.0)
        return round(surplus_pct / 100.0 * snapshot.limit_cents, 2)
    return 0.0


def snapshot_freshness(
    snapshot: AccountQuotaSnapshot,
    full_penalty_hours: float,
    now: datetime | None = None,
) -> float:
    """[0,1]：刚同步为 1，age ≥ full_penalty_hours 为 0；尺度 ≤ 0 时恒为 1。"""
    if full_penalty_hours <= 0:
        return 1.0
    now = now or datetime.now(timezone.utc)
    captured = snapshot.captured_at
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    age_hours = max((now - captured).total_seconds() / 3600.0, 0.0)
    return round(max(1.0 - age_hours / full_penalty_hours, 0.0), 4)


def _min_max(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def recommend_lenders(
    candidates: list[LenderCandidate],
    today: date | None = None,
    *,
    loan_selection: LoanSelectionConfig | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """硬过滤后按 urgency（日均待消化额度）主导的加权分排序。

    硬过滤：exhausted / 号主自身将耗尽 / 在借达上限 / 距作废不足 min_coverage_days。
    U（urgency）、S（surplus）池内 min-max 归一；L（在借人数）、F（快照新鲜度）
    本身即 [0,1] 绝对值直接入分。打平按 account_id 字典序保证确定性。
    """
    cfg = loan_selection or LoanSelectionConfig()
    today = today or date.today()
    now = now or datetime.now(timezone.utc)

    rows: list[dict] = []
    for cand in candidates:
        snapshot = cand.snapshot
        analysis = analyze_burn_rate(snapshot, today)
        if analysis.status == "exhausted" or analysis.exhausts_before_reset:
            continue
        if cand.active_loans >= cfg.max_active_loans_per_account:
            continue
        deadline = lender_deadline(snapshot.cycle_end, cand.renews_on)
        days = (deadline - today).days
        if days < cfg.min_coverage_days:
            continue
        surplus = projected_surplus_cents(snapshot, days, today)
        rows.append(
            {
                "candidate": cand,
                "analysis": analysis,
                "deadline": deadline,
                "days": days,
                "surplus": surplus,
                "urgency": surplus / max(days, 1),
                "load_factor": 1.0
                - cand.active_loans / max(cfg.max_active_loans_per_account, 1),
                "freshness": snapshot_freshness(
                    snapshot, cfg.freshness_full_penalty_hours, now
                ),
            }
        )
    if not rows:
        return []

    u_norm = _min_max([row["urgency"] for row in rows])
    s_norm = _min_max([row["surplus"] for row in rows])
    ranked: list[tuple[float, dict]] = []
    for idx, row in enumerate(rows):
        score = (
            cfg.weight_urgency * u_norm[idx]
            + cfg.weight_surplus * s_norm[idx]
            + cfg.weight_load * row["load_factor"]
            + cfg.weight_freshness * row["freshness"]
        )
        cand: LenderCandidate = row["candidate"]
        analysis: BurnRateAnalysis = row["analysis"]
        snapshot = cand.snapshot
        ranked.append(
            (
                score,
                {
                    "account_id": cand.account_id,
                    "account_identifier": cand.account_identifier,
                    "score": round(score, 4),
                    "deadline": row["deadline"].isoformat(),
                    "days_to_deadline": row["days"],
                    "renews_on": cand.renews_on.isoformat() if cand.renews_on else None,
                    "surplus_cents": row["surplus"],
                    "urgency_cents_per_day": round(row["urgency"], 2),
                    "active_loans": cand.active_loans,
                    "snapshot_freshness": row["freshness"],
                    "remaining_headroom_pct": analysis.remaining_headroom_pct,
                    "total_pct": snapshot.total_pct,
                    "auto_pct": snapshot.auto_pct,
                    "api_pct": snapshot.api_pct,
                    "api_limit_usd": analysis.api_limit_usd,
                    "days_until_reset": analysis.days_until_reset,
                    "status": analysis.status,
                    "cycle_start": snapshot.cycle_start.isoformat(),
                    "cycle_end": snapshot.cycle_end.isoformat(),
                },
            )
        )
    ranked.sort(key=lambda x: (-x[0], x[1]["account_id"]))
    return [item for _, item in ranked]
```

- [x] **Step 5: 运行 test_burn_rate.py 确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_burn_rate.py -v`
Expected: 14 passed

- [x] **Step 6: key_loans.py — 候选组装与推荐切换**

`pulse/tool_center/key_loans.py` 头部 import 改为：

```python
from sqlalchemy import func, select
```

```python
from pulse.config import LoanSelectionConfig
```

```python
from pulse.tool_center.burn_rate import (
    LenderCandidate,
    analyze_burn_rate,
    recommend_lenders,
)
```

在 `_latest_snapshots_by_account` 函数之后，**替换整个 `recommend_lender_for_borrower`（现 273-293 行）**为：

```python
def account_loan_deadline(account: AiAccount) -> date | None:
    """账号上借用 key 的自动回收日：额度重置日与订阅到期日取先到者。"""
    deadline = account.usage_resets_on
    if account.renews_on and (deadline is None or account.renews_on < deadline):
        deadline = account.renews_on
    return deadline


def _active_loan_counts_by_account(session: Session, team_id: str) -> dict[str, int]:
    rows = session.execute(
        select(KeyLoan.source_account_id, func.count())
        .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
        .where(AiAccount.team_id == team_id, KeyLoan.status == "active")
        .group_by(KeyLoan.source_account_id)
    ).all()
    return {account_id: count for account_id, count in rows}


def build_lender_candidates(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
) -> list[LenderCandidate]:
    """组装出借候选：最新快照 + renews_on + 当前在借人数。"""
    exclude_account_ids = exclude_account_ids or set()
    snapshots = _latest_snapshots_by_account(session, team_id)
    loan_counts = _active_loan_counts_by_account(session, team_id)
    repo = ToolCenterRepository(session, team_id)
    candidates: list[LenderCandidate] = []
    for account in repo.list_active_accounts():
        if not account.vendor or account.vendor.slug != "cursor":
            continue
        if account.id in exclude_account_ids:
            continue
        snap = snapshots.get(account.id)
        if not snap:
            continue
        candidates.append(
            LenderCandidate(
                snapshot=snap,
                account_id=account.id,
                account_identifier=account.account_identifier,
                renews_on=account.renews_on,
                active_loans=loan_counts.get(account.id, 0),
            )
        )
    return candidates


def recommend_lender_for_borrower(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
    today: date | None = None,
    loan_selection: LoanSelectionConfig | None = None,
) -> dict | None:
    candidates = build_lender_candidates(
        session, team_id, exclude_account_ids=exclude_account_ids
    )
    ranked = recommend_lenders(candidates, today, loan_selection=loan_selection)
    return ranked[0] if ranked else None
```

`pulse/web/quota_api.py`：import 行改为

```python
from pulse.tool_center.key_loans import (
    KeyLoanError,
    KeyLoanService,
    build_lender_candidates,
    issue_loan_key,
    loan_payload,
)
```

`quota_recommend`（现 154-166 行）函数体改为：

```python
    def quota_recommend(session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        today = date.today()
        candidates = build_lender_candidates(session, team.id)
        return recommend_lenders(
            candidates, today, loan_selection=config.tool_center.loan_selection
        )
```

- [x] **Step 7: 运行新测试与受影响旧测试**

Run: `.venv/Scripts/python -m pytest tests/test_lender_selection.py tests/test_burn_rate.py tests/test_key_loan_dingtalk.py tests/test_quota_api.py -v`
Expected: all passed

- [x] **Step 8: 全量回归**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: all passed（若有与本改动无关的历史失败，记录并在继续前向用户确认）

- [x] **Step 9: 提交**

```bash
git add pulse/tool_center/burn_rate.py pulse/tool_center/key_loans.py pulse/web/quota_api.py tests/test_burn_rate.py tests/test_lender_selection.py
git commit -m "feat(tool-center): deadline-driven lender scoring with urgency-first digestion"
```

---

### Task 3: 发放人数上限 + 并发锁 + 配置透传

**Files:**
- Modify: `pulse/tool_center/key_loans.py`（`issue_loan_key` 加上限复查与锁；`request_self_service_loan` 透传配置）
- Modify: `pulse/tool_center/key_loan_ops.py`（`request_loan_payload` 透传配置）
- Modify: `pulse/web/quota_api.py`（`loan_key` 路由透传配置）
- Test: `tests/test_key_loan_caps.py`（新建）
- Test: `tests/test_quota_api.py`（追加路由用例）

- [x] **Step 1: 写失败测试 — 新建 tests/test_key_loan_caps.py**

```python
from __future__ import annotations

import base64
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from pulse.config import LoanSelectionConfig
from pulse.ingestion.credentials import CredentialService
from pulse.storage.db import init_db
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.key_loans import (
    KeyLoanError,
    KeyLoanService,
    issue_loan_key,
    recommend_lender_for_borrower,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo, mock_cursor_key_exchange

TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def cap_env():
    session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    team, repo = make_team_repo(session)
    seed_v2_catalog(session, team)
    admin = repo.add_member("cap-admin", "CapAdmin")
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    lender = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    session.add(
        AccountQuotaSnapshot(
            account_id=lender.id,
            captured_at=datetime.now(timezone.utc),
            cycle_start=date(2026, 7, 1),
            cycle_end=date(2026, 8, 1),
            limit_cents=7000,
            used_cents=1000,
            remaining_cents=6000,
            total_pct=14.0,
        )
    )
    repo.commit()
    yield {
        "session": session,
        "repo": repo,
        "admin": admin,
        "lender": lender,
    }
    session.close()


def _mock_client(env) -> MagicMock:
    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_cap_test_loan_key"}
    mock_client.list_user_api_keys.return_value = [{"id": 42, "name": "pulse-loan"}]
    mock_cursor_key_exchange(
        mock_client, email=env["lender"].account_identifier.lower()
    )
    return mock_client


def _bind_lender_primary(env, mock_client) -> None:
    cred_service = CredentialService(env["session"], TEST_KEY, cursor_client=mock_client)
    cred_service.bind_cursor_api_key(
        account_id=env["lender"].id,
        api_key="crsr_primary_key_for_cap_test_abcdefghij",
        member_id=env["admin"].id,
    )
    env["session"].flush()


def _add_borrower(env, suffix: str):
    member = env["repo"].add_member(f"cap-b{suffix}", f"CapB{suffix}")
    env["session"].flush()
    return member


def _issue(env, mock_client, borrower, loan_selection=None):
    return issue_loan_key(
        env["session"],
        TEST_KEY,
        team_id=env["repo"].team_id,
        source_account_id=env["lender"].id,
        borrower_member_id=borrower.id,
        bound_by_member_id=env["admin"].id,
        cursor_client=mock_client,
        loan_selection=loan_selection,
    )


def test_two_loans_allowed_then_third_rejected(cap_env):
    env = cap_env
    mock_client = _mock_client(env)
    _bind_lender_primary(env, mock_client)
    b1, b2, b3 = (_add_borrower(env, str(i)) for i in (1, 2, 3))

    first = _issue(env, mock_client, b1)
    second = _issue(env, mock_client, b2)
    assert first["loan_id"] != second["loan_id"]

    with pytest.raises(KeyLoanError, match="名额已满"):
        _issue(env, mock_client, b3)

    loan_svc = KeyLoanService(env["session"], TEST_KEY, cursor_client=mock_client)
    assert len(loan_svc.list_active_loans()) == 2


def test_cap_is_configurable(cap_env):
    env = cap_env
    mock_client = _mock_client(env)
    _bind_lender_primary(env, mock_client)
    b1 = _add_borrower(env, "1")
    b2 = _add_borrower(env, "2")
    selection = LoanSelectionConfig(max_active_loans_per_account=1)

    _issue(env, mock_client, b1, loan_selection=selection)
    with pytest.raises(KeyLoanError, match="名额已满"):
        _issue(env, mock_client, b2, loan_selection=selection)


def test_recommend_then_full_account_is_rejected_at_issue(cap_env):
    env = cap_env
    mock_client = _mock_client(env)
    _bind_lender_primary(env, mock_client)
    b1, b2, b3 = (_add_borrower(env, str(i)) for i in (1, 2, 3))

    lender = recommend_lender_for_borrower(env["session"], env["repo"].team_id)
    assert lender is not None
    assert lender["account_id"] == env["lender"].id

    _issue(env, mock_client, b1)
    _issue(env, mock_client, b2)
    # 推荐发生在名额占满之前：发放必须在事务内复查，而不是信任推荐结果
    with pytest.raises(KeyLoanError, match="名额已满"):
        _issue(env, mock_client, b3)
```

- [x] **Step 2: 追加路由测试到 tests/test_quota_api.py 文件末尾**

```python
def test_loan_key_enforces_account_cap(quota_env):
    client = quota_env["client"]
    config = quota_env["config"]
    owner = quota_env["owner"]
    account = quota_env["cursor_account"]
    borrower = quota_env["borrower"]
    token = create_access_token(config, owner)

    mock_client = MagicMock()
    mock_client.get_access_token.return_value = "session-token"
    mock_client.create_user_api_key.return_value = {"apiKey": "crsr_loan_key_plaintext_value"}
    mock_client.list_user_api_keys.return_value = [{"id": 99, "name": "pulse-loan-Borrower"}]
    from tests.conftest import mock_cursor_key_exchange

    mock_cursor_key_exchange(mock_client, email=account.account_identifier.lower())

    with patch("pulse.tool_center.key_loans.CursorApiClient", return_value=mock_client):
        s = quota_env["session_factory"]()
        cred_service = CredentialService(s, TEST_KEY, cursor_client=mock_client)
        cred_service.bind_cursor_api_key(
            account_id=account.id,
            api_key="crsr_primary_key_for_loan_test_abcdefghij",
            member_id=owner.id,
        )
        s.commit()
        s.close()

        url = f"/api/v2/accounts/{account.id}/loan-key"
        for _ in range(2):
            res = client.post(
                url,
                headers=_headers(token),
                json={"borrower_member_id": borrower.id, "auto_revoke_on_reset": True},
            )
            assert res.status_code == 200

        res = client.post(
            url,
            headers=_headers(token),
            json={"borrower_member_id": borrower.id, "auto_revoke_on_reset": True},
        )
        assert res.status_code == 400
        assert "名额已满" in res.json()["detail"]
```

- [x] **Step 3: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_key_loan_caps.py tests/test_quota_api.py::test_loan_key_enforces_account_cap -v`
Expected: FAIL（第三笔发放未抛 `KeyLoanError`；路由第三次返回 200 而非 400）

- [x] **Step 4: 实现上限复查与并发锁**

`pulse/tool_center/key_loans.py`：

import 行补充 `update`：

```python
from sqlalchemy import func, select, update
```

在 `_resolve_remote_key_id` 函数之前插入锁辅助函数：

```python
def _lock_account_for_loan_issue(session: Session, account_id: str) -> None:
    """串行化同一账号的并发发放。

    Postgres：SELECT ... FOR UPDATE 锁账号行；
    sqlite：一次 no-op 写操作抢占写锁，并发写者排队（busy timeout 后重见最新计数）。
    必须在 COUNT 在借人数之前调用。
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            select(AiAccount.id).where(AiAccount.id == account_id).with_for_update()
        )
    else:
        session.execute(
            update(AiAccount)
            .where(AiAccount.id == account_id)
            .values(updated_at=AiAccount.updated_at)
        )
```

`issue_loan_key` 签名末尾（`cursor_client` 之后）增加参数：

```python
    loan_selection: LoanSelectionConfig | None = None,
```

在 `issue_loan_key` 内、exhausted 校验（`raise KeyLoanError("借出账号套内额度已耗尽...")`）之后、`borrower_name = ...` 之前插入：

```python
    selection = loan_selection or LoanSelectionConfig()
    _lock_account_for_loan_issue(session, source_account_id)
    active_loan_count = (
        session.scalar(
            select(func.count(KeyLoan.id)).where(
                KeyLoan.source_account_id == source_account_id,
                KeyLoan.status == "active",
            )
        )
        or 0
    )
    if active_loan_count >= selection.max_active_loans_per_account:
        raise KeyLoanError("该账号借用名额已满，请选择其他账号")
```

`request_self_service_loan` 签名末尾（`cursor_client` 之后）增加参数：

```python
    loan_selection: LoanSelectionConfig | None = None,
```

其函数体内两处调用改为透传：

```python
    lender = recommend_lender_for_borrower(
        session,
        team_id,
        exclude_account_ids={a.id for a in own_accounts},
        loan_selection=loan_selection,
    )
```

```python
    return issue_loan_key(
        session,
        encryption_key,
        team_id=team_id,
        source_account_id=lender["account_id"],
        borrower_member_id=borrower.id,
        bound_by_member_id=bound_by_member_id or borrower.id,
        note=note or "钉钉自助借 Key",
        auto_revoke_on_reset=True,
        cursor_client=cursor_client,
        loan_selection=loan_selection,
    )
```

`pulse/tool_center/key_loan_ops.py` 的 `request_loan_payload` 中调用改为：

```python
        result = request_self_service_loan(
            repo.session,
            enc_key,
            team_id=repo.team_id,
            borrower=member,
            note=note,
            loan_selection=config.tool_center.loan_selection,
        )
```

`pulse/web/quota_api.py` 的 `loan_key` 路由中 `issue_loan_key(...)` 调用增加一个关键字实参：

```python
                loan_selection=config.tool_center.loan_selection,
```

- [x] **Step 5: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_key_loan_caps.py tests/test_quota_api.py tests/test_key_loan_dingtalk.py -v`
Expected: all passed

- [x] **Step 6: 提交**

```bash
git add pulse/tool_center/key_loans.py pulse/tool_center/key_loan_ops.py pulse/web/quota_api.py tests/test_key_loan_caps.py tests/test_quota_api.py
git commit -m "feat(tool-center): enforce per-account loan cap with in-transaction recheck and write lock"
```

---

### Task 4: 按 deadline 回收 + loan_expires_on 展示 + 规格收尾

**Files:**
- Modify: `pulse/tool_center/key_loans.py`（`expire_loans_on_reset` 改按 deadline；`issue_loan_key` 返回值与 `loan_payload` 增加 `loan_expires_on`）
- Modify: `pulse/tool_center/key_loan_ops.py`（`_loan_item_dict`、`read_self_loan`、`request_loan`、`request_loan_payload`）
- Modify: `docs/superpowers/specs/2026-07-21-loan-lender-selection-design.md`（§3 措辞 + 状态）
- Test: `tests/test_lender_selection.py`（追加回收/payload/文案用例）

- [x] **Step 1: 写失败测试 — 追加到 tests/test_lender_selection.py**

文件头部 import 区补充：

```python
from pulse.config import AppConfig, CredentialConfig, TenantConfig
from pulse.tool_center.key_loan_ops import read_self_loan
from pulse.tool_center.key_loans import (
    KeyLoanService,
    build_lender_candidates,
    loan_payload,
    recommend_lender_for_borrower,
)
```

（即把原先只 import `build_lender_candidates, recommend_lender_for_borrower` 的那一行替换为上面四行。）

文件末尾追加：

```python
def test_expire_loans_when_renews_on_passed(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    loan = _make_loan(session, env, account, env["member"])
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date.today() + timedelta(days=20),
        renews_on=date.today() - timedelta(days=1),
    )

    svc = KeyLoanService(session, TEST_KEY)
    assert svc.expire_loans_on_reset() == 1
    assert loan.status == "expired"


def test_expire_skips_loans_before_deadline(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    loan = _make_loan(session, env, account, env["member"])
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date.today() + timedelta(days=20),
        renews_on=date.today() + timedelta(days=5),
    )

    svc = KeyLoanService(session, TEST_KEY)
    assert svc.expire_loans_on_reset() == 0
    assert loan.status == "active"


def test_loan_payload_exposes_loan_expires_on(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    loan = _make_loan(session, env, account, env["member"])
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date(2026, 8, 1),
        renews_on=date(2026, 7, 25),
    )

    payload = loan_payload(loan, session)
    assert payload["loan_expires_on"] == "2026-07-25"


def test_read_self_loan_shows_expire_date(lender_env):
    env = lender_env
    session = env["session"]
    account = env["accounts"][0]
    borrower = env["member"]
    _make_loan(session, env, account, borrower)
    env["tool_repo"].update_account(
        account.id,
        usage_resets_on=date(2026, 8, 1),
        renews_on=date(2026, 7, 25),
    )
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=TEST_KEY),
    )

    text = read_self_loan(env["repo"], config, borrower)
    assert "自动回收日：2026-07-25" in text
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_lender_selection.py -v`
Expected: FAIL（`expire_loans_on_reset` 返回 0；`loan_payload` 无 `loan_expires_on` 键，KeyError；文案无"自动回收日"）

- [x] **Step 3: 实现回收扩展与字段透传**

`pulse/tool_center/key_loans.py` 的 `expire_loans_on_reset`（现 193-211 行）整体替换为：

```python
    def expire_loans_on_reset(self, today: date | None = None) -> int:
        today = today or date.today()
        expired = 0
        loans = self.list_active_loans()
        for loan in loans:
            if not loan.auto_revoke_on_reset:
                continue
            account = self.session.get(AiAccount, loan.source_account_id)
            if not account:
                continue
            deadline = account_loan_deadline(account)
            if not deadline or deadline > today:
                continue
            try:
                self.revoke_loan(loan.id, revoke_remote=True)
                loan.status = "expired"
                expired += 1
            except Exception:
                continue
        return expired
```

`issue_loan_key` 的 return dict（现 371-379 行）：在 `return {` 之前一行插入 deadline 计算，dict 内增加字段：

```python
    primary_member_name = None
    if account.primary_member_id:
        primary = session.get(Member, account.primary_member_id)
        primary_member_name = primary.display_name if primary else None
    deadline = account_loan_deadline(account)
    return {
        "loan_id": loan.id,
        "api_key": loan_api_key,
        "key_hint": loan_cred.key_hint,
        "borrower_name": borrower.display_name,
        "source_account_identifier": account.account_identifier,
        "primary_member_name": primary_member_name,
        "loan_expires_on": deadline.isoformat() if deadline else None,
        "warning": "可随时发送「我的借用」再次查看 Key。借用消耗为账号用量差值近似，非精确按 Key 统计。",
    }
```

`loan_payload`（现 446-481 行）：在 `borrowed_cents = max(...)` 计算之后、`return {` 之前插入 deadline 计算，dict 内增加字段：

```python
    deadline = account_loan_deadline(account) if account else None
```

return dict 中 `"auto_revoke_on_reset"` 一行之后插入：

```python
        "loan_expires_on": deadline.isoformat() if deadline else None,
```

`pulse/tool_center/key_loan_ops.py`：

`_loan_item_dict` 的 return dict 中 `"auto_revoke_on_reset"` 一行之后插入：

```python
        "loan_expires_on": base.get("loan_expires_on"),
```

`read_self_loan` 的 `lines` 列表中，`重置日自动回收` 一行之后插入：

```python
            f"自动回收日：{loan.get('loan_expires_on') or '—'}",
```

`request_loan` 的成功文案改为：

```python
        return (
            "✅ 已为你分配临时 Key：\n"
            f"借出人：{lender_name}\n"
            f"Key：{payload['api_key']}\n"
            f"自动回收日：{payload.get('loan_expires_on') or '—'}\n\n"
            f"{payload.get('warning') or ''}\n"
            "归还请发送：归还 Key"
        )
```

`request_loan_payload` 的 ok dict 中 `"loan_id"` 一行之后插入：

```python
            "loan_expires_on": result.get("loan_expires_on"),
```

- [x] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_lender_selection.py tests/test_key_loan_dingtalk.py tests/test_quota_api.py -v`
Expected: all passed

- [x] **Step 5: 规格文档收尾（§3 归一化措辞 + 状态）**

`docs/superpowers/specs/2026-07-21-loan-lender-selection-design.md` 两处编辑：

1. 头部状态行：`**状态：** 待审阅` → `**状态：** 已批准并实施`
2. §3 中"四个因子在候选池内分别 min-max 归一到 [0,1]（单候选时该项取 1；该项全池相等时取 1）："一句改为："U、S 两个额度因子在候选池内 min-max 归一到 [0,1]（单候选或全池相等时该项取 1）；L、F 本身即 [0,1] 绝对值，直接入分（对 F 做 min-max 会把快照时间的微差噪声放大成满分差距）："

- [x] **Step 6: 全量回归**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: all passed

- [x] **Step 7: 提交**

```bash
git add pulse/tool_center/key_loans.py pulse/tool_center/key_loan_ops.py tests/test_lender_selection.py docs/superpowers/specs/2026-07-21-loan-lender-selection-design.md
git commit -m "feat(tool-center): expire loans at min(usage_resets_on, renews_on) and surface loan_expires_on"
```

---

## 自查记录（计划撰写时已完成）

- **规格覆盖：** §1 deadline（Task 2 `lender_deadline`/`account_loan_deadline`）、§2 硬过滤（Task 2 `recommend_lenders`）、§3 打分（Task 2，归一化措辞按实现细化修订于 Task 4 Step 5）、§4 上限与并发（Task 3）、§5 回收与展示（Task 4）、§6 改动清单（Tasks 1-4 全覆盖，`scheduler.py` 确认无需改接线）、§7 配置（Task 1）、§8 边界（Task 2 各单测）、§9 测试计划（各任务测试步骤）、§10 验收标准（Task 2/3/4 测试 + 全量回归）。
- **并发说明：** 真实并发竞态由 `_lock_account_for_loan_issue` + 事务内复查保证；测试以"推荐后名额被占满再发放"的顺序场景验证复查逻辑（sqlite `:memory:` 多线程并发测试不可靠，不引入）。
- **既有行为保持：** 号主保护（exhausted/exhausts_before_reset）过滤不变；自助借用准入（自有账号 warning/exhausted、一人一笔、绑 Key）不变；管理员路径不新增"借用者仅一笔"限制（该规则仅自助层，与现状一致）。
