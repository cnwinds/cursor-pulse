from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")

from pulse.config import AppConfig, CredentialConfig, InternalApiConfig, TenantConfig
from pulse.storage.models import AccountQuotaSnapshot, Base, CapabilityInvocationRow
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from pulse.web.app import create_app
from tests.conftest import make_team_repo

INTERNAL_TOKEN = "pulse-internal-test-token"


@pytest.fixture
def api_env():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        credentials=CredentialConfig(encryption_key=""),
        internal=InternalApiConfig(service_token=INTERNAL_TOKEN),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = sf()
    team, repo = make_team_repo(session)
    actor = repo.add_member("actor-user", "Actor")
    other = repo.add_member("other-user", "Other")
    seed_v2_catalog(session, team)
    session.flush()

    tool_repo = ToolCenterRepository(session, team.id)
    cursor_accounts = [a for a in tool_repo.list_accounts() if a.vendor.slug == "cursor"]
    actor_account = cursor_accounts[0]
    other_account = cursor_accounts[1]
    tool_repo.update_account(actor_account.id, primary_member_id=actor.id, status="shared")
    tool_repo.update_account(other_account.id, primary_member_id=other.id, status="shared")

    snap = AccountQuotaSnapshot(
        account_id=actor_account.id,
        captured_at=datetime.now(timezone.utc),
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        limit_cents=7000,
        used_cents=2000,
        remaining_cents=5000,
        total_pct=28.5,
    )
    session.add(snap)
    repo.commit()
    session.close()

    client = TestClient(create_app(config, sf))
    return {
        "client": client,
        "config": config,
        "team": team,
        "actor": actor,
        "session_factory": sf,
    }


def _auth_headers(token: str | None = INTERNAL_TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _invoke_body(*, team_id: str, actor_member_id: str, idempotency_key: str = "idem-1") -> dict:
    return {
        "invocation_id": "inv-1",
        "idempotency_key": idempotency_key,
        "team_id": team_id,
        "actor_member_id": actor_member_id,
        "capability_key": "quota.self.read",
        "capability_version": "1",
        "arguments": {},
    }


def test_internal_api_rejects_missing_token(api_env):
    response = api_env["client"].get("/api/internal/v1/capabilities/manifest")
    assert response.status_code == 401


def test_internal_api_rejects_wrong_token(api_env):
    response = api_env["client"].get(
        "/api/internal/v1/capabilities/manifest",
        headers=_auth_headers("wrong-token"),
    )
    assert response.status_code == 401


def test_internal_api_rejects_when_token_unconfigured():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        internal=InternalApiConfig(service_token=""),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    client = TestClient(create_app(config, sf))

    response = client.get(
        "/api/internal/v1/capabilities/manifest",
        headers=_auth_headers("any-token"),
    )
    assert response.status_code in (401, 503)


def test_manifest_lists_three_capabilities(api_env):
    response = api_env["client"].get(
        "/api/internal/v1/capabilities/manifest",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    keys = {op["capability_key"] for op in response.json()["operations"]}
    assert keys >= {"quota.self.read", "cursor.key.bind", "guide_image.update"}


def test_manifest_accepts_x_pulse_internal_token(api_env):
    response = api_env["client"].get(
        "/api/internal/v1/capabilities/manifest",
        headers={"X-Pulse-Internal-Token": INTERNAL_TOKEN},
    )
    assert response.status_code == 200
    keys = {op["capability_key"] for op in response.json()["operations"]}
    assert keys >= {"quota.self.read", "cursor.key.bind", "guide_image.update"}


def test_invoke_quota_self_read_succeeds(api_env):
    body = _invoke_body(
        team_id=api_env["team"].id,
        actor_member_id=api_env["actor"].id,
    )
    response = api_env["client"].post(
        "/api/internal/v1/capabilities/invoke",
        headers=_auth_headers(),
        json=body,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert len(payload["result"]["accounts"]) == 1


def test_invoke_duplicate_idempotency_returns_same_result(api_env):
    body = _invoke_body(
        team_id=api_env["team"].id,
        actor_member_id=api_env["actor"].id,
        idempotency_key="idem-dup",
    )
    client = api_env["client"]
    first = client.post(
        "/api/internal/v1/capabilities/invoke",
        headers=_auth_headers(),
        json=body,
    )
    assert first.status_code == 200

    body["invocation_id"] = "inv-2"
    second = client.post(
        "/api/internal/v1/capabilities/invoke",
        headers=_auth_headers(),
        json=body,
    )
    assert second.status_code == 200
    assert second.json() == first.json()

    session = api_env["session_factory"]()
    try:
        count = session.scalar(
            select(func.count())
            .select_from(CapabilityInvocationRow)
            .where(
                CapabilityInvocationRow.team_id == api_env["team"].id,
                CapabilityInvocationRow.idempotency_key == "idem-dup",
            )
        )
        assert count == 1
    finally:
        session.close()


def test_invoke_handles_concurrent_idempotency_conflict(api_env, monkeypatch):
    from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
    from pulse.capabilities import invocation_store
    from pulse.web import internal_capabilities_api as api_module

    team_id = api_env["team"].id
    actor_id = api_env["actor"].id
    idem = "idem-race"

    session = api_env["session_factory"]()
    try:
        winner_request = CapabilityInvokeRequest(
            invocation_id="inv-winner",
            idempotency_key=idem,
            team_id=team_id,
            actor_member_id=actor_id,
            capability_key="quota.self.read",
            capability_version="1",
        )
        winner_result = CapabilityInvokeResult(
            status="succeeded",
            user_message="cached-from-winner",
            result={"accounts": []},
        )
        invocation_store.save_invocation(
            session, request=winner_request, result=winner_result
        )
        session.commit()
    finally:
        session.close()

    original_get = api_module.get_by_idempotency
    get_calls = {"n": 0}

    def race_get(session, *, team_id, idempotency_key):
        get_calls["n"] += 1
        if get_calls["n"] == 1:
            return None
        return original_get(session, team_id=team_id, idempotency_key=idempotency_key)

    monkeypatch.setattr(api_module, "get_by_idempotency", race_get)

    body = _invoke_body(
        team_id=team_id,
        actor_member_id=actor_id,
        idempotency_key=idem,
    )
    body["invocation_id"] = "inv-loser"
    response = api_env["client"].post(
        "/api/internal/v1/capabilities/invoke",
        headers=_auth_headers(),
        json=body,
    )
    assert response.status_code == 200
    assert response.json()["user_message"] == "cached-from-winner"


def test_get_invocation_by_id(api_env):
    body = _invoke_body(
        team_id=api_env["team"].id,
        actor_member_id=api_env["actor"].id,
        idempotency_key="idem-get",
    )
    invoke = api_env["client"].post(
        "/api/internal/v1/capabilities/invoke",
        headers=_auth_headers(),
        json=body,
    )
    assert invoke.status_code == 200

    response = api_env["client"].get(
        "/api/internal/v1/capabilities/invocations/inv-1",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


def test_get_unknown_invocation_returns_404(api_env):
    response = api_env["client"].get(
        "/api/internal/v1/capabilities/invocations/missing-id",
        headers=_auth_headers(),
    )
    assert response.status_code == 404


def test_invoke_usage_query_uses_team_assistant_llm_settings(api_env, monkeypatch):
    """Web 后台 assistant_llm 配置应作用于 internal invoke，与钉钉对话共用同一套设置。"""
    from pulse.web.settings_store import patch_team_setting

    session = api_env["session_factory"]()
    try:
        patch_team_setting(
            session,
            team_id=api_env["team"].id,
            section="assistant_llm",
            patch={
                "enabled": True,
                "api_key": "team-llm-key",
                "model": "deepseek-v4-pro",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            member_id=api_env["actor"].id,
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "pulse.capabilities.handlers.usage_query.answer_usage_with_llm",
        lambda *args, **kwargs: "7 月各模型 token 汇总如下。",
    )

    body = {
        "invocation_id": "inv-usage-q",
        "idempotency_key": "idem-usage-q",
        "team_id": api_env["team"].id,
        "actor_member_id": api_env["actor"].id,
        "capability_key": "usage.query",
        "capability_version": "1",
        "arguments": {"text": "帮我查下7月份所有模型的token使用情况"},
    }
    response = api_env["client"].post(
        "/api/internal/v1/capabilities/invoke",
        headers=_auth_headers(),
        json=body,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert "token" in payload["user_message"].lower()
