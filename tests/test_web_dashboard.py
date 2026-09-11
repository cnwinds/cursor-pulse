import pytest

pytest.importorskip("fastapi")
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import AccountQuotaSnapshot, UsageDailyAggregate
from pulse.tool_center.ingestion_status import period_date_range
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_module_web_client, make_team_repo, make_test_session_factory


@pytest.fixture(scope="module")
def _dash_app():
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client, proxy = make_module_web_client(config)
    return client, config, proxy


@pytest.fixture
def dash_client(_dash_app):
    client, config, proxy = _dash_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    _team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    repo.add_member("u1", "Bob")
    repo.commit()
    s.close()
    return client, config, owner


def test_dashboard_overview(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    h = {"Authorization": f"Bearer {token}"}
    res = client.get("/api/dashboard/overview", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert "ingestion" in body
    assert body["ingestion"]["active_count"] >= 0
    assert "submitted_count" in body["ingestion"]
    assert "sync_stats" in body
    assert "summary" in body
    assert "pending_actions" in body
    assert body["pending_actions"]["total_count"] >= 0


def test_system_schedule(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/schedule", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    job_ids = {job["id"] for job in body["jobs"]}
    assert "cursor_sync_tick" in job_ids
    assert "expire_key_loans" not in job_ids
    assert "collection_start" not in job_ids
    assert "monthly_report" not in job_ids


def test_system_integrations(dash_client):
    client, config, owner = dash_client
    token = create_access_token(config, owner)
    res = client.get("/api/system/integrations", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "dingtalk" in body
    assert "feishu" in body
    assert "bot_platform" in body
    assert "im_group_configured" in body
    assert "assistant_llm" in body
    assert "runtime_note" in body
    assert "pulse_llm" not in body


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
    usage = sections["usage"]
    pstart, pend = period_date_range(usage["period"])
    tz_name = usage.get("timezone") or config.collection.timezone
    end = min(datetime.now(ZoneInfo(tz_name)).date(), pend)
    assert usage["start"] == pstart.isoformat()
    assert usage["end"] == end.isoformat()
    assert len(usage["series_by_day"]) == (end - pstart).days + 1
    assert sections["sync"]["total_accounts"] == 0
    assert sections["recent_activity"]["items"] == []
    assert isinstance(sections["integrations"]["im_group_configured"], bool)


def test_dashboard_overview_sections_permission_trimming(dash_client_with_roles):
    client, config, _owner, viewer, accountant = dash_client_with_roles

    viewer_token = create_access_token(config, viewer)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {viewer_token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["sections"] == {}
    # 顶层遗留字段同样按权限裁剪：viewer 无任何数据权限
    assert "ingestion" not in body
    assert "submission" not in body
    assert "sync_stats" not in body
    assert "summary" not in body

    acct_token = create_access_token(config, accountant)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {acct_token}"})
    assert res.status_code == 200
    body = res.json()
    assert set(body["sections"]) == {"quota", "usage", "loans", "sync"}
    # accountant 有 accounts:read（可见同步计数），无 settings:read（不见 summary）
    assert "ingestion" in body
    assert "sync_stats" in body
    assert "summary" not in body


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


def test_dashboard_overview_does_not_build_full_ingestion_payload(
    dash_client_with_roles, monkeypatch
):
    def _boom(*args, **kwargs):
        raise AssertionError("full ingestion payload should not run for dashboard")

    monkeypatch.setattr(
        "pulse.tool_center.ingestion_status.build_ingestion_status_payload", _boom
    )
    monkeypatch.setattr(
        "pulse.tool_center.usage_analytics.build_usage_analytics_overview", _boom
    )
    monkeypatch.setattr("pulse.web.dashboard_api.settings_for_api", _boom)
    client, config, owner, _viewer, _acct = dash_client_with_roles
    token = create_access_token(config, owner)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["sections"]["sync"] is not None
    assert res.json()["sections"]["usage"] is not None


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
    tz = ZoneInfo(config.collection.timezone)
    today = datetime.now(tz).date()
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
    last = usage["series_by_day"][-1]
    assert last["date"] == today.isoformat()
    assert last["tokens_input"] == 1000
    assert last["tokens_output"] == 500
    assert last["tokens_cache_read"] == 200
    assert last["tokens_total"] == 1700


def test_dashboard_daily_series_matches_usage_analytics(_dash_app):
    """Overview and usage-analytics share the same calendar-day Token/cost series."""
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
    tz = ZoneInfo(config.collection.timezone)
    today = datetime.now(tz).date()
    pstart, pend = period_date_range(today.strftime("%Y-%m"))
    end = min(today, pend)
    earlier = max(pstart, today - timedelta(days=3))
    s.add_all(
        [
            UsageDailyAggregate(
                account_id=cursor_account.id,
                event_date=earlier,
                model="composer-low",
                event_count=4,
                total_cost_usd=12.5,
                tokens_input=10_000,
                tokens_output=2_000,
                tokens_cache_read=80_000,
            ),
            UsageDailyAggregate(
                account_id=cursor_account.id,
                event_date=end,
                model="composer-high",
                event_count=5,
                total_cost_usd=280.0852,
                tokens_input=50_100_000,
                tokens_output=4_500_000,
                tokens_cache_read=685_000_000,
            ),
        ]
    )
    repo.commit()
    s.close()

    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    dash = client.get("/api/dashboard/overview", headers=headers)
    assert dash.status_code == 200
    usage = dash.json()["sections"]["usage"]
    assert usage["start"] == pstart.isoformat()
    assert usage["end"] == end.isoformat()
    assert len(usage["series_by_day"]) == (end - pstart).days + 1

    analytics = client.get(
        "/api/v2/usage-analytics/overview",
        params={"start": usage["start"], "end": usage["end"]},
        headers=headers,
    )
    assert analytics.status_code == 200
    series = analytics.json()["series_by_day"]
    assert [d["date"] for d in usage["series_by_day"]] == [d["date"] for d in series]
    for left, right in zip(usage["series_by_day"], series):
        assert left["tokens_total"] == right["tokens_total"]
        assert left["tokens_input"] == right["tokens_input"]
        assert left["tokens_output"] == right["tokens_output"]
        assert left["tokens_cache_read"] == right["tokens_cache_read"]
        assert left["cost_usd"] == right["cost_usd"]
    by_date = {d["date"]: d for d in usage["series_by_day"]}
    high_total = 739_600_000
    low_total = 92_000
    if earlier == end:
        assert by_date[end.isoformat()]["tokens_total"] == high_total + low_total
    else:
        assert by_date[end.isoformat()]["tokens_total"] == high_total
        assert by_date[earlier.isoformat()]["tokens_total"] == low_total


def test_dashboard_overview_quota_risk_top(_dash_app):
    client, config, proxy = _dash_app
    sf = make_test_session_factory()
    proxy.bind(sf)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, channel_user_id="a1", display_name="A", password="x")
    seed_v2_catalog(s, team)
    s.flush()
    tool_repo = ToolCenterRepository(s, team.id)
    exhausted_acc = next(a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor")
    tool_repo.update_account(exhausted_acc.id, status="shared")
    plans = tool_repo.list_plans(vendor_id=exhausted_acc.vendor_id)
    warning_acc = tool_repo.create_account(
        vendor_id=exhausted_acc.vendor_id,
        plan_id=plans[0].id,
        account_identifier="warn@example.com",
        ownership="company",
        status="shared",
    )
    today = date.today()
    s.add(
        AccountQuotaSnapshot(
            account_id=exhausted_acc.id,
            captured_at=datetime.now(timezone.utc),
            cycle_start=today - timedelta(days=5),
            cycle_end=today + timedelta(days=25),
            limit_cents=7000,
            used_cents=7700,
            remaining_cents=0,
            total_pct=110.0,
        )
    )
    s.add(
        AccountQuotaSnapshot(
            account_id=warning_acc.id,
            captured_at=datetime.now(timezone.utc),
            cycle_start=today - timedelta(days=5),
            cycle_end=today + timedelta(days=25),
            limit_cents=7000,
            used_cents=5950,
            remaining_cents=1050,
            total_pct=85.0,
        )
    )
    repo.commit()
    s.close()

    token = create_access_token(config, owner)
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    quota = res.json()["sections"]["quota"]
    assert quota["exhausted_count"] == 1
    assert quota["warning_count"] == 1
    risk_top = quota["risk_top"]
    assert len(risk_top) == 2
    # exhausted 排在 warning 前
    assert risk_top[0]["status"] == "exhausted"
    assert risk_top[1]["status"] == "warning"
    assert risk_top[0]["account_id"] == exhausted_acc.id
    assert risk_top[0]["quota_progress"] == 1.1
    assert risk_top[1]["quota_progress"] == 0.85
    for item in risk_top:
        assert {"account_id", "account_identifier", "status", "quota_progress"} <= set(item)
