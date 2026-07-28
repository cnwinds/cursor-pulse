# 概览页重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **注意：本计划不包含 git commit 步骤** —— 所有改动留在工作区，由用户审查后自行提交。

**Goal:** 重写 web-admin 概览页为「异常待办 + KPI + 用量趋势」混合式仪表盘，数据由扩展后的 `/api/dashboard/overview` 聚合接口按登录用户权限裁剪提供。

**Architecture:** 后端在 `pulse/web/dashboard_api.py` 新增 `build_dashboard_sections`，复用 quota-board / usage-analytics / ingestion-status / loans / proxy / audit 的现有 builder，按 actor 权限裁剪各 section，单 section 失败降级为 null；前端重写 `DashboardView.vue`，抽取 `StatCard` 组件与共享 echarts 注册模块，路由与侧边栏对概览放宽为「登录即可」。

**Tech Stack:** FastAPI + SQLAlchemy + pytest（后端）；Vue 3 + Element Plus + echarts 6 + vue-echarts 8 + Pinia（前端）。

**Spec:** `docs/superpowers/specs/2026-07-28-dashboard-redesign-design.md`

---

## 文件结构

**后端：**
- 修改 `pulse/web/quota_api.py` — 提取 `build_quota_board_items()` / `count_active_loans()` 为模块级可复用函数，路由改为调用它们（纯重构，行为不变）
- 修改 `pulse/web/dashboard_api.py` — 新增 7 个 section builder + `build_dashboard_sections()`，`build_dashboard_overview` 返回新增 `sections` 键
- 修改 `pulse/web/app.py:213-221` — `/api/dashboard/overview` 依赖从 `require_capability("settings:read")` 放宽为 `_require_user`
- 修改 `tests/test_web_dashboard.py` — 新增 sections 聚合 / 权限裁剪 / 失败隔离 / 种子数据正确性测试

**前端：**
- 创建 `web-admin/src/utils/echarts.ts` — 共享 echarts 按需注册（从 UsageAnalyticsView 挪出）
- 创建 `web-admin/src/components/StatCard.vue` — 统一 KPI 卡片
- 修改 `web-admin/src/views/UsageAnalyticsView.vue:201-224` — 改用共享 echarts 模块
- 重写 `web-admin/src/views/DashboardView.vue` — 新概览页
- 修改 `web-admin/src/router/index.ts:33` — 概览路由去掉 `permission: 'settings:read'`
- 修改 `web-admin/src/layouts/MainLayout.vue:20` — 侧边栏「概览」对所有登录用户可见
- 修改 `CHANGELOG.md` — [Unreleased] 记录本次变更

**设计细化（spec 未显式列出、实现时补足的决策）：**
- `sections.sync`（权限 `accounts:read`）：同步异常计数来自 `build_ingestion_status_payload` 的 summary，是「需要关注」中同步类条目的数据源
- KPI 卡「活跃账号」取 `sections.sync`（而非顶层 `ingestion`），保证无 `accounts:read` 的用户看不到账号计数
- `sections.usage`：KPI 为账期至今（period_start ~ today），趋势为 `series_by_day` 末 14 天切片
- `quota_progress` 量纲为 0~1（`pulse/tool_center/burn_rate.py:28-34`），前端 ×100 后给 `el-progress`
- （代码评审后修正）顶层旧字段同样按权限裁剪：`summary` 仅 `settings:read`、`ingestion`/`submission`/`sync_stats` 仅 `accounts:read`，与 sections 权限语义一致；内置角色行为不变
- （代码评审后修正）`_usage_section` 用 `datetime.now(ZoneInfo(timezone_name)).date()` 取团队时区当天，修掉月初边界静默降级

**sections 数据契约（前后端一致，必须严格遵守）：**

| 键 | 权限 | 字段 |
|---|---|---|
| `quota` | `accounts:read` | `exhausted_count / warning_count / healthy_count / unknown_count: int`，`risk_top: [{account_id, account_identifier, primary_member_name, status, quota_progress(0~1), projected_exhaustion_date, days_until_reset}]`（最多 5 条，仅 exhausted/warning） |
| `usage` | `accounts:read` | `period, start, end: str`，`tokens_total / event_count: int`，`cost_usd: float`，`series_by_day: [{date, tokens_input, tokens_output, tokens_cache_read, tokens_total, event_count, cost_usd}]`（≤14 条） |
| `loans` | `accounts:read` | `active_count: int` |
| `sync` | `accounts:read` | `total_accounts / submitted_count / synced / sync_failed / sync_stale / no_credential / missing_primary / unsubmitted: int` |
| `proxy` | `proxy:read` | `active_key_count / total_tokens: int`，`total_cost_usd: float` |
| `integrations` | `settings:read` | `bot_platform: str`，`im_group_configured: bool`，`issues: [{key, label}]` |
| `recent_activity` | `audit:read` | `items: [{id, operator_name, action_label, detail, created_at, ...}]`（≤10 条） |

无权限的键整个不出现；构建失败的键值为 `null`。

---

## Task 1: 后端重构 — 提取 quota/loans 可复用构建函数

把 `quota_board` 路由内的看板构建逻辑和 `list_loans` 内的 active_count 查询提取为模块级函数，供 Task 2 的 dashboard 聚合复用。纯重构，行为不变，靠现有测试保障。

**Files:**
- Modify: `pulse/web/quota_api.py`（在 `_status_rank`（:155-156）之后插入两个函数；改写 `quota_board`（:164-186）与 `list_loans` 的 active_count 部分（:224-229））
- Test: `tests/test_quota_api.py`（现有测试，不改动）

- [ ] **Step 1: 先跑现有测试确认基线绿**

Run: `.venv/Scripts/python -m pytest tests/test_quota_api.py -q`
Expected: 全部 PASS（如有预先存在的失败，记录后继续，不得由本任务引入新失败）

- [ ] **Step 2: 在 `_status_rank` 之后插入两个模块级函数**

```python
def build_quota_board_items(session: Session, team_id: str) -> list[dict]:
    repo = ToolCenterRepository(session, team_id)
    today = date.today()
    snapshots = _latest_snapshots_by_account(session, team_id)
    accounts = [
        account
        for account in repo.list_active_accounts()
        if account.vendor and account.vendor.slug == "cursor"
    ]
    member_ids = {a.primary_member_id for a in accounts if a.primary_member_id}
    member_names: dict[str, str] = {}
    if member_ids:
        members = session.scalars(
            select(Member).where(Member.id.in_(member_ids))
        ).all()
        member_names = {m.id: m.display_name for m in members}
    items = []
    for account in accounts:
        snapshot = snapshots.get(account.id)
        items.append(_board_item(account, snapshot, today, member_names=member_names))
    items.sort(key=lambda x: (_status_rank(x["status"]), -(x.get("quota_progress") or 0)))
    return items


def count_active_loans(session: Session, team_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(AiAccount.team_id == team_id, KeyLoan.status == "active")
        )
        or 0
    )
```

- [ ] **Step 3: `quota_board` 路由改为调用提取的函数**

将路由体（原 :165-186）替换为：

```python
    def quota_board(session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        return build_quota_board_items(session, team.id)
```

- [ ] **Step 4: `list_loans` 中的 active_count 改为调用 `count_active_loans`**

将原 :224-229 的 inline 查询替换为：

```python
        active_count = count_active_loans(session, team.id)
```

- [ ] **Step 5: 重跑测试确认重构无回归**

Run: `.venv/Scripts/python -m pytest tests/test_quota_api.py -q`
Expected: 与 Step 1 结果一致（全 PASS）

---

## Task 2: 后端 — dashboard sections 聚合（owner 全量）

**Files:**
- Modify: `pulse/web/dashboard_api.py`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_web_dashboard.py` 末尾追加（文件头部已有 `import pytest` 与 conftest helpers 导入，无需新增 import）：

```python
@pytest.fixture
def dash_client_with_roles(_dash_app):
    client, config, proxy = _dash_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    _team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    viewer = repo.add_member("viewer", "Viewer")
    viewer.portal_role = "ai_member"
    viewer.portal_status = "active"
    accountant = repo.add_member("acct", "Acct")
    accountant.portal_role = "custom"
    accountant.portal_permissions = ["accounts:read"]
    accountant.portal_status = "active"
    repo.commit()
    s.close()
    return client, config, owner, viewer, accountant


def test_dashboard_overview_sections_owner(dash_client_with_roles):
    client, config, owner, _viewer, _acct = dash_client_with_roles
    token = create_access_token(config, owner)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    sections = res.json()["sections"]
    assert set(sections) == {
        "quota", "usage", "loans", "sync", "proxy", "integrations", "recent_activity",
    }
    assert sections["quota"]["exhausted_count"] == 0
    assert sections["quota"]["risk_top"] == []
    assert sections["loans"]["active_count"] == 0
    assert sections["proxy"]["active_key_count"] == 0
    assert sections["usage"]["tokens_total"] == 0
    assert isinstance(sections["usage"]["series_by_day"], list)
    assert len(sections["usage"]["series_by_day"]) <= 14
    assert sections["sync"]["total_accounts"] == 0
    assert sections["recent_activity"]["items"] == []
    assert isinstance(sections["integrations"]["im_group_configured"], bool)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py::test_dashboard_overview_sections_owner -q`
Expected: FAIL（`KeyError: 'sections'`）

- [ ] **Step 3: 实现 sections 聚合**

`pulse/web/dashboard_api.py` 顶部 imports 补充（现有 imports 保留）：

```python
import logging
from datetime import date

from sqlalchemy import select
```

`logger` 定义加在 imports 之后：

```python
logger = logging.getLogger(__name__)
```

在 `build_dashboard_overview` 之前插入：

```python
def _safe_section(section_name: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("dashboard section %s build failed", section_name)
        return None


def _quota_section(session: Session, team_id: str) -> dict:
    from pulse.web.quota_api import build_quota_board_items

    items = build_quota_board_items(session, team_id)
    key_by_status = {
        "exhausted": "exhausted_count",
        "warning": "warning_count",
        "healthy": "healthy_count",
        "unknown": "unknown_count",
    }
    counts = {"exhausted_count": 0, "warning_count": 0, "healthy_count": 0, "unknown_count": 0}
    for item in items:
        counts[key_by_status.get(item["status"], "unknown_count")] += 1
    risk_top = [
        {
            "account_id": item["account_id"],
            "account_identifier": item["account_identifier"],
            "primary_member_name": item.get("primary_member_name"),
            "status": item["status"],
            "quota_progress": item.get("quota_progress"),
            "projected_exhaustion_date": item.get("projected_exhaustion_date"),
            "days_until_reset": item.get("days_until_reset"),
        }
        for item in items
        if item["status"] in ("exhausted", "warning")
    ][:5]
    return {**counts, "risk_top": risk_top}


def _usage_section(
    config: AppConfig,
    session: Session,
    team_id: str,
    period: str,
    timezone_name: str,
) -> dict:
    from pulse.tool_center.ingestion_status import period_date_range
    from pulse.tool_center.usage_analytics import build_usage_analytics_overview

    period_start, period_end = period_date_range(period)
    end = min(date.today(), period_end)
    overview = build_usage_analytics_overview(
        session,
        team_id,
        start=period_start,
        end=end,
        timezone=timezone_name,
        top_n=5,
    )
    kpi = overview["kpi"]
    return {
        "period": period,
        "start": overview["start"],
        "end": overview["end"],
        "tokens_total": kpi["tokens_total"],
        "cost_usd": kpi["cost_usd"],
        "event_count": kpi["event_count"],
        "series_by_day": overview["series_by_day"][-14:],
    }


def _loans_section(session: Session, team_id: str) -> dict:
    from pulse.web.quota_api import count_active_loans

    return {"active_count": count_active_loans(session, team_id)}


def _sync_section(session: Session, team_id: str, period: str, actor: Member) -> dict:
    from pulse.tool_center.ingestion_status import build_ingestion_status_payload

    payload = build_ingestion_status_payload(session, team_id, period, actor)
    s = payload["summary"]
    return {
        "total_accounts": s["total_accounts"],
        "submitted_count": s["submitted_count"],
        "synced": s.get("synced", 0),
        "sync_failed": s.get("sync_failed", 0),
        "sync_stale": s.get("sync_stale", 0),
        "no_credential": s.get("no_credential", 0),
        "missing_primary": s.get("missing_primary", 0),
        "unsubmitted": s.get("unsubmitted", 0),
    }


def _proxy_section(session: Session) -> dict:
    from pulse.proxy import service as proxy_service
    from pulse.storage.models import ProxyKey

    keys = session.scalars(select(ProxyKey).where(ProxyKey.status == "active")).all()
    total_tokens = 0
    total_cents = 0
    for key in keys:
        tokens, cents = proxy_service.total_usage(session, key.id)
        total_tokens += tokens
        total_cents += cents
    return {
        "active_key_count": len(keys),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cents / 100.0, 2),
    }


def _integrations_section(config: AppConfig, session: Session, team_id: str) -> dict:
    full = build_integrations_status(config, session, team_id)
    issues = []
    if not full["im_group_configured"]:
        issues.append({"key": "im_group", "label": "IM 工作群未配置"})
    return {
        "bot_platform": full["bot_platform"],
        "im_group_configured": full["im_group_configured"],
        "issues": issues,
    }


def _activity_section(session: Session, team_id: str) -> dict:
    from pulse.web.audit import list_admin_audit_logs

    return {"items": list_admin_audit_logs(session, team_id, limit=10)}


def build_dashboard_sections(
    config: AppConfig,
    session: Session,
    team_id: str,
    *,
    period: str,
    timezone_name: str,
    actor: Member,
) -> dict:
    sections: dict[str, dict | None] = {}
    if has_permission(actor, "accounts:read"):
        sections["quota"] = _safe_section("quota", _quota_section, session, team_id)
        sections["usage"] = _safe_section(
            "usage", _usage_section, config, session, team_id, period, timezone_name
        )
        sections["loans"] = _safe_section("loans", _loans_section, session, team_id)
        sections["sync"] = _safe_section("sync", _sync_section, session, team_id, period, actor)
    if has_permission(actor, "proxy:read"):
        sections["proxy"] = _safe_section("proxy", _proxy_section, session)
    if has_permission(actor, "settings:read"):
        sections["integrations"] = _safe_section(
            "integrations", _integrations_section, config, session, team_id
        )
    if has_permission(actor, "audit:read"):
        sections["recent_activity"] = _safe_section(
            "recent_activity", _activity_section, session, team_id
        )
    return sections
```

在 `build_dashboard_overview` 的 return dict 末尾（`"pending_actions": ...` 之后）追加：

```python
        "sections": (
            build_dashboard_sections(
                config,
                session,
                team_id,
                period=period,
                timezone_name=effective["collection"]["timezone"],
                actor=actor,
            )
            if actor
            else {}
        ),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py::test_dashboard_overview_sections_owner -q`
Expected: PASS

- [ ] **Step 5: 跑整个 dashboard 测试文件确认无回归**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py -q`
Expected: 全部 PASS（原有 `test_dashboard_overview` 用 owner token，sections 全量返回，旧断言不受影响）

---

## Task 3: 后端 — 权限裁剪与路由放宽

**Files:**
- Modify: `pulse/web/app.py:213-221`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_web_dashboard.py` 末尾追加：

```python
def test_dashboard_overview_sections_permission_trimming(dash_client_with_roles):
    client, config, _owner, viewer, accountant = dash_client_with_roles

    viewer_token = create_access_token(config, viewer)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {viewer_token}"})
    assert res.status_code == 200
    assert res.json()["sections"] == {}

    acct_token = create_access_token(config, accountant)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {acct_token}"})
    assert res.status_code == 200
    assert set(res.json()["sections"]) == {"quota", "usage", "loans", "sync"}


def test_dashboard_overview_requires_login(dash_client):
    client, _config, _owner = dash_client
    res = client.get("/api/dashboard/overview")
    assert res.status_code == 401


def test_dashboard_overview_section_failure_isolated(dash_client_with_roles, monkeypatch):
    from pulse.web import dashboard_api

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dashboard_api, "_usage_section", _boom)
    client, config, owner, _viewer, _acct = dash_client_with_roles
    token = create_access_token(config, owner)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    sections = res.json()["sections"]
    assert sections["usage"] is None
    assert sections["quota"] is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py::test_dashboard_overview_sections_permission_trimming -q`
Expected: FAIL（viewer 请求返回 403 —— 路由仍要求 `settings:read`）

（`test_dashboard_overview_section_failure_isolated` 此时应已 PASS —— Task 2 的实现自带失败隔离。）

- [ ] **Step 3: 放宽路由依赖**

`pulse/web/app.py:213-221` 替换为：

```python
    @app.get("/api/dashboard/overview")
    def dashboard_overview(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(_require_user),
    ):
        team, repo = _team_repo(session)
        return build_dashboard_overview(
            config, session, team.id, repo=repo, actor=user.member
        )
```

（去掉两处 `Depends(require_capability("settings:read"))`，改为注入 `_require_user`；`PortalUser` 已在 app.py:21 导入。）

- [ ] **Step 4: 跑测试确认全部通过**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py -q`
Expected: 全部 PASS

---

## Task 4: 后端 — 种子数据验证 usage 聚合正确性

**Files:**
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: 写失败测试**

`tests/test_web_dashboard.py` 顶部 imports 补充：

```python
from datetime import date

from pulse.storage.models import UsageDailyAggregate
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
```

文件末尾追加：

```python
def test_dashboard_overview_usage_section_with_data(_dash_app):
    client, config, proxy = _dash_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    seed_v2_catalog(s, team)
    s.flush()
    tool_repo = ToolCenterRepository(s, team.id)
    cursor_account = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    today = date.today()
    s.add(
        UsageDailyAggregate(
            account_id=cursor_account.id,
            event_date=today,
            model="claude-4-sonnet",
            event_count=3,
            total_cost_usd=1.5,
            tokens_input=1000,
            tokens_output=500,
            tokens_cache_read=200,
        )
    )
    repo.commit()
    s.close()

    token = create_access_token(config, owner)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    usage = res.json()["sections"]["usage"]
    assert usage["tokens_total"] == 1700
    assert usage["cost_usd"] == 1.5
    assert usage["event_count"] == 3
    assert usage["series_by_day"][-1]["date"] == today.isoformat()
```

- [ ] **Step 2: 跑测试确认通过（本测试对 Task 2 实现应直接通过；若失败则修实现）**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py::test_dashboard_overview_usage_section_with_data -q`
Expected: PASS。若 FAIL，检查 `_usage_section` 的日期范围（`period_date_range(period)` 是否覆盖 today）与 `series_by_day` 切片，修正后重跑。

- [ ] **Step 3: 后端全量回归**

Run: `.venv/Scripts/python -m pytest tests/test_web_dashboard.py tests/test_quota_api.py tests/test_usage_analytics.py tests/test_ingestion_status_api.py -q`
Expected: 全部 PASS（若 `tests/test_ingestion_status_api.py` 不存在则从命令中去掉）

---

## Task 5: 前端 — echarts 共享模块 + StatCard 组件

**Files:**
- Create: `web-admin/src/utils/echarts.ts`
- Create: `web-admin/src/components/StatCard.vue`
- Modify: `web-admin/src/views/UsageAnalyticsView.vue:201-224`

- [ ] **Step 1: 创建 `web-admin/src/utils/echarts.ts`**

```ts
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'

// 共享的 echarts 按需注册：任何页面 import 本模块即完成注册，避免各视图重复 use(...)
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
])
```

- [ ] **Step 2: 创建 `web-admin/src/components/StatCard.vue`**

```vue
<template>
  <el-card shadow="never" class="stat-card">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value">{{ value }}</div>
    <div v-if="sub" class="stat-sub">{{ sub }}</div>
  </el-card>
</template>

<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  sub?: string
}>()
</script>

<style scoped>
.stat-card {
  margin-bottom: 16px;
}
.stat-label {
  color: #64748b;
  font-size: 13px;
}
.stat-value {
  font-size: 28px;
  font-weight: 600;
  margin-top: 8px;
}
.stat-sub {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 6px;
}
</style>
```

- [ ] **Step 3: UsageAnalyticsView 改用共享模块**

`web-admin/src/views/UsageAnalyticsView.vue:201-224` 的原代码：

```ts
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { formatSpend, formatTokens } from '@/utils/usage'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
])
```

替换为：

```ts
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { formatSpend, formatTokens } from '@/utils/usage'
```

- [ ] **Step 4: 构建验证**

Run: `cd web-admin && npm run build`
Expected: 构建成功（`vite build` 无报错）

---

## Task 6: 前端 — 重写 DashboardView

**Files:**
- Modify: `web-admin/src/views/DashboardView.vue`（整文件替换）

- [ ] **Step 1: 整文件替换 `web-admin/src/views/DashboardView.vue`**

```vue
<template>
  <div v-loading="loading" class="dashboard">
    <el-alert v-if="loadError" type="error" :closable="false" class="load-error">
      <template #title>
        概览数据加载失败
        <el-button size="small" class="retry-btn" @click="reload">重试</el-button>
      </template>
    </el-alert>

    <template v-else-if="data">
      <div class="page-toolbar">
        <el-button
          size="small"
          :icon="Refresh"
          circle
          :loading="refreshing"
          title="刷新"
          @click="reload"
        />
      </div>

      <!-- ① 需要关注 -->
      <el-card v-if="attentionItems.length" shadow="never" class="block">
        <template #header>
          <div class="card-header">
            <span>需要关注</span>
            <el-badge :value="attentionItems.length" type="warning" />
          </div>
        </template>
        <div class="attention-list">
          <div v-for="item in attentionItems" :key="item.key" class="attention-item">
            <el-icon :color="item.level === 'danger' ? '#f56c6c' : '#e6a23c'">
              <WarningFilled />
            </el-icon>
            <span class="attention-text">{{ item.text }}</span>
            <router-link :to="item.to" class="attention-link">去处理 →</router-link>
          </div>
        </div>
      </el-card>

      <!-- ② KPI 卡片 -->
      <el-row v-if="statCards.length" :gutter="16">
        <el-col v-for="card in statCards" :key="card.label" :xs="12" :sm="8" :md="6">
          <StatCard :label="card.label" :value="card.value" :sub="card.sub" />
        </el-col>
      </el-row>

      <!-- ③ 用量趋势 -->
      <el-card v-if="usage" shadow="never" class="block" header="近 14 天用量趋势">
        <v-chart v-if="hasTrend" class="trend-chart" :option="trendOption" autoresize />
        <el-empty v-else description="近 14 天暂无用量数据" :image-size="60" />
      </el-card>

      <!-- ④ 额度风险 / 最近动态 -->
      <el-row v-if="quotaRiskTop.length || activityItems.length" :gutter="16">
        <el-col v-if="quotaRiskTop.length" :xs="24" :md="12">
          <el-card shadow="never" class="block">
            <template #header>
              <div class="card-header">
                <span>额度风险 Top 5</span>
                <router-link to="/quota-board" class="more-link">看板 →</router-link>
              </div>
            </template>
            <div v-for="row in quotaRiskTop" :key="row.account_id" class="risk-item">
              <div class="risk-head">
                <span class="risk-name">{{ row.account_identifier }}</span>
                <el-tag :type="row.status === 'exhausted' ? 'danger' : 'warning'" size="small">
                  {{ row.status === 'exhausted' ? '已耗尽' : '预警' }}
                </el-tag>
              </div>
              <el-progress
                :percentage="riskPct(row)"
                :status="row.status === 'exhausted' ? 'exception' : 'warning'"
              />
              <div class="risk-meta">
                <template v-if="row.primary_member_name">负责人 {{ row.primary_member_name }} · </template>
                <span v-if="row.projected_exhaustion_date">预计 {{ row.projected_exhaustion_date }} 耗尽</span>
                <span v-else-if="row.days_until_reset != null">{{ row.days_until_reset }} 天后重置</span>
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col v-if="activityItems.length" :xs="24" :md="12">
          <el-card shadow="never" class="block">
            <template #header>
              <div class="card-header">
                <span>最近动态</span>
                <router-link to="/audit" class="more-link">审计 →</router-link>
              </div>
            </template>
            <div v-for="row in activityItems" :key="row.id" class="activity-item">
              <div class="activity-title">
                <span class="activity-operator">{{ row.operator_name }}</span>
                <span>{{ row.action_label }}</span>
                <span v-if="row.detail" class="activity-detail">{{ row.detail }}</span>
              </div>
              <div class="activity-time">{{ formatChinaTime(row.created_at) }}</div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-empty v-if="!hasAnyContent" description="暂无概览数据：你的账号暂无可查看的数据区块" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import client from '@/api/client'
import StatCard from '@/components/StatCard.vue'
import { formatChinaTime } from '@/utils/time'
import { formatCompactTokens, formatSpend, formatTokensM } from '@/utils/usage'

interface QuotaRiskItem {
  account_id: string
  account_identifier: string
  primary_member_name?: string | null
  status: string
  quota_progress?: number | null
  projected_exhaustion_date?: string | null
  days_until_reset?: number | null
}

interface QuotaSection {
  exhausted_count: number
  warning_count: number
  healthy_count: number
  unknown_count: number
  risk_top: QuotaRiskItem[]
}

interface TrendDay {
  date: string
  tokens_total: number
  cost_usd: number
}

interface UsageSection {
  period: string
  start: string
  end: string
  tokens_total: number
  cost_usd: number
  event_count: number
  series_by_day: TrendDay[]
}

interface LoansSection {
  active_count: number
}

interface SyncSection {
  total_accounts: number
  submitted_count: number
  sync_failed: number
  no_credential: number
  missing_primary: number
  unsubmitted: number
}

interface ProxySection {
  active_key_count: number
  total_tokens: number
  total_cost_usd: number
}

interface IntegrationsSection {
  bot_platform: string
  im_group_configured: boolean
  issues: { key: string; label: string }[]
}

interface ActivityItem {
  id: number
  operator_name: string
  action_label: string
  detail?: string | null
  created_at: string
}

interface OverviewData {
  period: string
  pending_actions?: { total_count: number } | null
  sections?: {
    quota?: QuotaSection | null
    usage?: UsageSection | null
    loans?: LoansSection | null
    sync?: SyncSection | null
    proxy?: ProxySection | null
    integrations?: IntegrationsSection | null
    recent_activity?: { items: ActivityItem[] } | null
  }
}

interface AttentionItem {
  key: string
  text: string
  to: string
  level: 'danger' | 'warning'
}

const loading = ref(false)
const refreshing = ref(false)
const loadError = ref(false)
const data = ref<OverviewData | null>(null)

const sections = computed(() => data.value?.sections ?? {})
const quota = computed(() => sections.value.quota ?? null)
const usage = computed(() => sections.value.usage ?? null)
const loans = computed(() => sections.value.loans ?? null)
const sync = computed(() => sections.value.sync ?? null)
const proxy = computed(() => sections.value.proxy ?? null)
const integrations = computed(() => sections.value.integrations ?? null)
const activity = computed(() => sections.value.recent_activity ?? null)

const attentionItems = computed<AttentionItem[]>(() => {
  const items: AttentionItem[] = []
  const q = quota.value
  if (q) {
    if (q.exhausted_count > 0) {
      items.push({ key: 'quota-exhausted', text: `${q.exhausted_count} 个账号额度已耗尽`, to: '/quota-board', level: 'danger' })
    }
    if (q.warning_count > 0) {
      items.push({ key: 'quota-warning', text: `${q.warning_count} 个账号额度预警`, to: '/quota-board', level: 'warning' })
    }
  }
  const s = sync.value
  if (s) {
    if (s.sync_failed > 0) {
      items.push({ key: 'sync-failed', text: `${s.sync_failed} 个账号同步失败`, to: '/accounts', level: 'danger' })
    }
    if (s.no_credential > 0) {
      items.push({ key: 'no-credential', text: `${s.no_credential} 个账号未配置凭证`, to: '/accounts', level: 'warning' })
    }
    if (s.missing_primary > 0) {
      items.push({ key: 'missing-primary', text: `${s.missing_primary} 个账号待绑定负责人`, to: '/accounts', level: 'warning' })
    }
    if (s.unsubmitted > 0) {
      items.push({ key: 'unsubmitted', text: `${s.unsubmitted} 个账号本账期待同步`, to: '/accounts', level: 'warning' })
    }
  }
  const pending = data.value?.pending_actions
  if (pending && pending.total_count > 0) {
    items.push({ key: 'pending-users', text: `${pending.total_count} 个后台用户待审批`, to: '/users', level: 'warning' })
  }
  for (const issue of integrations.value?.issues ?? []) {
    items.push({ key: `integration-${issue.key}`, text: issue.label, to: '/settings', level: 'warning' })
  }
  return items
})

const statCards = computed(() => {
  const cards: { label: string; value: string; sub?: string }[] = []
  const s = sync.value
  if (s) {
    const pct = s.total_accounts ? Math.round((s.submitted_count / s.total_accounts) * 100) : 0
    cards.push({
      label: '活跃账号',
      value: String(s.total_accounts),
      sub: `已同步 ${s.submitted_count}/${s.total_accounts} · ${pct}%`,
    })
  }
  const u = usage.value
  if (u) {
    cards.push({ label: '本账期花费', value: formatSpend(u.cost_usd), sub: `账期 ${u.period}` })
    cards.push({
      label: '本账期 Tokens',
      value: formatTokensM(u.tokens_total),
      sub: `${u.event_count} 次事件`,
    })
  }
  if (loans.value) {
    cards.push({ label: '当前借出', value: String(loans.value.active_count), sub: '进行中的 Key 借用' })
  }
  const p = proxy.value
  if (p) {
    cards.push({
      label: '活跃代理 Key',
      value: String(p.active_key_count),
      sub: `累计 ${formatSpend(p.total_cost_usd)}`,
    })
  }
  const q = quota.value
  if (q) {
    cards.push({
      label: '额度告急',
      value: String(q.exhausted_count + q.warning_count),
      sub: `耗尽 ${q.exhausted_count} · 预警 ${q.warning_count}`,
    })
  }
  return cards
})

const trendDays = computed<TrendDay[]>(() => usage.value?.series_by_day ?? [])
const hasTrend = computed(() => trendDays.value.some((d) => d.tokens_total > 0 || d.cost_usd > 0))

const trendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['Tokens', '花费'] },
  grid: { left: 12, right: 12, top: 32, bottom: 8, containLabel: true },
  xAxis: { type: 'category', data: trendDays.value.map((d) => d.date.slice(5)) },
  yAxis: [
    {
      type: 'value',
      name: 'Tokens',
      axisLabel: { formatter: (v: number) => formatCompactTokens(v) || '0' },
    },
    {
      type: 'value',
      name: '花费 $',
      axisLabel: { formatter: (v: number) => `$${v}` },
    },
  ],
  series: [
    {
      name: 'Tokens',
      type: 'bar',
      data: trendDays.value.map((d) => d.tokens_total),
      itemStyle: { color: '#3b82f6' },
    },
    {
      name: '花费',
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      data: trendDays.value.map((d) => d.cost_usd),
      itemStyle: { color: '#10b981' },
    },
  ],
}))

const quotaRiskTop = computed<QuotaRiskItem[]>(() => quota.value?.risk_top ?? [])
const activityItems = computed<ActivityItem[]>(() => activity.value?.items ?? [])

const hasAnyContent = computed(
  () =>
    attentionItems.value.length > 0 ||
    statCards.value.length > 0 ||
    Boolean(usage.value) ||
    Boolean(quota.value) ||
    Boolean(activity.value),
)

function riskPct(row: QuotaRiskItem) {
  return Math.min(100, Math.round((row.quota_progress ?? 0) * 100))
}

async function reload() {
  refreshing.value = true
  loadError.value = false
  try {
    const res = await client.get('/api/dashboard/overview')
    data.value = res.data
  } catch {
    loadError.value = true
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  loading.value = true
  await reload()
  loading.value = false
})
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.page-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
.more-link {
  margin-left: auto;
  font-size: 13px;
  font-weight: 400;
  color: var(--el-color-primary);
  text-decoration: none;
}
.attention-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.attention-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.attention-text {
  color: #334155;
}
.attention-link {
  margin-left: auto;
  font-size: 13px;
  color: var(--el-color-primary);
  text-decoration: none;
  white-space: nowrap;
}
.trend-chart {
  height: 280px;
}
.risk-item + .risk-item {
  margin-top: 14px;
}
.risk-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.risk-name {
  font-size: 13px;
  font-weight: 500;
}
.risk-meta {
  font-size: 12px;
  color: #64748b;
  margin-top: 4px;
}
.activity-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f1f5f9;
}
.activity-item:last-child {
  border-bottom: none;
}
.activity-title {
  font-size: 13px;
  color: #334155;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.activity-operator {
  font-weight: 600;
}
.activity-detail {
  color: #64748b;
}
.activity-time {
  font-size: 12px;
  color: #94a3b8;
  white-space: nowrap;
}
.load-error {
  margin-bottom: 16px;
}
.retry-btn {
  margin-left: 8px;
}
</style>
```

注意：`channelMeta` 与旧的 `pending-card`/同步进度条样式随之删除，属预期（旧「运行概览」与进度条已按 spec 收纳/砍除）。

- [ ] **Step 2: 构建验证**

Run: `cd web-admin && npm run build`
Expected: 构建成功，无 TS/模板报错

---

## Task 7: 前端 — 路由与侧边栏权限放宽

**Files:**
- Modify: `web-admin/src/router/index.ts:33`
- Modify: `web-admin/src/layouts/MainLayout.vue:20`

- [ ] **Step 1: 路由 meta 去掉权限要求**

`web-admin/src/router/index.ts:29-34`：

```ts
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
          meta: { permission: 'settings:read', title: '概览' },
        },
```

改为：

```ts
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
          meta: { title: '概览' },
        },
```

（`router/index.ts:150-153` 的守卫对无 `meta.permission` 的路由仅要求登录，无需改动。）

- [ ] **Step 2: 侧边栏「概览」对所有登录用户可见**

`web-admin/src/layouts/MainLayout.vue:20-23`：

```vue
          <el-menu-item v-if="auth.hasPermission('settings:read')" index="/">
            <el-icon><Odometer /></el-icon>
            <span>概览</span>
          </el-menu-item>
```

改为：

```vue
          <el-menu-item index="/">
            <el-icon><Odometer /></el-icon>
            <span>概览</span>
          </el-menu-item>
```

- [ ] **Step 3: 构建验证**

Run: `cd web-admin && npm run build`
Expected: 构建成功

---

## Task 8: 收尾 — CHANGELOG + 全量验证

**Files:**
- Modify: `CHANGELOG.md:5-6`

- [ ] **Step 1: CHANGELOG 记录**

`CHANGELOG.md` 的 `## [Unreleased]` 下追加：

```markdown
## [Unreleased]

### 新增

- **概览页重设计**：聚合「需要关注」异常待办（额度告急 / 同步异常 / 待审批 / 集成未配置）、核心 KPI、近 14 天用量趋势、额度风险 Top 5 与最近动态；`/api/dashboard/overview` 扩展为按登录用户权限裁剪的聚合接口，概览页对所有登录用户开放
```

- [ ] **Step 2: 后端全量测试**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 全部 PASS；若有与本改动无关的预存失败，与基线比对确认非本次引入

- [ ] **Step 3: 前端构建**

Run: `cd web-admin && npm run build`
Expected: 构建成功（产物输出至 `pulse/web/static`，属项目既定构建流程）

- [ ] **Step 4: 人工走查（交给用户）**

启动 `pulse web` 后用 owner 登录 `/admin/`，确认：需要关注、KPI 卡、趋势图、额度风险、最近动态五块均正常；再用低权限账号确认区块裁剪与空状态。

---

## Self-Review 记录

- **Spec 覆盖**：需要关注（quota/sync/pending_actions/integrations → Task 6 attentionItems）✓；KPI 卡（sync/usage/loans/proxy/quota → Task 6 statCards）✓；趋势图（usage.series_by_day → Task 6）✓；额度风险 Top5 / 最近动态（Task 6）✓；后端聚合 + 权限裁剪 + 失败降级（Task 2/3）✓；路由放宽（Task 3 后端 + Task 7 前端）✓；测试（Task 2/3/4）✓；YAGNI 项均未实现 ✓
- **类型一致性**：sections 键名与字段名在 Task 2（后端 builder）、Task 2/3/4（测试断言）、Task 6（前端 interface）三处一致；`build_quota_board_items` / `count_active_loans` 在 Task 1 定义、Task 2 引用一致；`formatChinaTime` / `formatSpend` / `formatTokensM` / `formatCompactTokens` 与 `web-admin/src/utils/time.ts`、`usage.ts` 实际导出名一致
- **占位符扫描**：无 TBD/TODO；所有代码块完整
