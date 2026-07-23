from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import AssistantConfig
from assistant_platform.storage.db import init_assistant_db

TEAM_ID = "t1"
MEMBER_ID = "member-default"
TOKEN = "secret"


@pytest.fixture
def client():
    cfg = AssistantConfig(
        service_token=TOKEN,
        team_id=TEAM_ID,
        pulse_base_url="http://pulse.test",
        pulse_internal_token="pulse-tok",
    )
    sf = init_assistant_db("sqlite://", team_id=TEAM_ID)
    app = create_assistant_app(cfg, sf)
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _invoke_body(**overrides) -> dict:
    body = {
        "team_id": TEAM_ID,
        "actor_member_id": MEMBER_ID,
        "role": "ai_member",
        "capability_key": "guide_image.update",
        "arguments": {},
        "confirmed": True,
    }
    body.update(overrides)
    return body


def test_invoke_forbidden_without_capability_access(client):
    resp = client.post(
        "/api/assistant/v1/capabilities/invoke",
        json=_invoke_body(),
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["error_code"] == "forbidden"


def test_invoke_requires_service_token(client):
    resp = client.post(
        "/api/assistant/v1/capabilities/invoke",
        json=_invoke_body(),
    )
    assert resp.status_code == 401


def test_me_returns_seeded_default_member_capabilities(client):
    from assistant_platform.capabilities.catalog import SELF_SERVICE_KEYS

    resp = client.get(
        "/api/assistant/v1/capabilities/me",
        params={
            "team_id": TEAM_ID,
            "member_id": MEMBER_ID,
            "role": "ai_member",
            "channel": "dingtalk",
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()}
    assert keys == set(SELF_SERVICE_KEYS)
    assert "guide_image.update" not in keys
    assert "usage.aggregate" not in keys

def test_catalog_non_empty(client):
    resp = client.get(
        "/api/assistant/v1/capabilities/catalog",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    catalog = resp.json()
    assert len(catalog) >= 3
    keys = {item["key"] for item in catalog}
    assert "quota.self.read" in keys
    assert "cursor.key.bind" in keys
    assert "guide_image.update" in keys


def test_list_packs_returns_seeded_packs(client):
    resp = client.get(
        "/api/assistant/v1/capabilities/packs",
        params={"team_id": TEAM_ID},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    packs = resp.json()
    assert len(packs) >= 2
    keys = {item["key"] for item in packs}
    assert "cursor_self_service" in keys
    assert "assistant_owner" in keys
    owner_pack = next(item for item in packs if item["key"] == "assistant_owner")
    assert "report.publish" in owner_pack["capability_keys"]


def test_list_assignments_returns_rows(client):
    client.post(
        "/api/assistant/v1/capabilities/assignments",
        json={
            "team_id": TEAM_ID,
            "scope_type": "user_deny",
            "scope_id": MEMBER_ID,
            "capability_key": "cursor.key.bind",
        },
        headers=_auth_headers(),
    )
    resp = client.get(
        "/api/assistant/v1/capabilities/assignments",
        params={"team_id": TEAM_ID},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert any(r["capability_key"] == "cursor.key.bind" for r in rows)


def test_user_deny_assignment_removes_bind_from_me(client):
    me_before = client.get(
        "/api/assistant/v1/capabilities/me",
        params={"team_id": TEAM_ID, "member_id": MEMBER_ID, "role": "ai_member"},
        headers=_auth_headers(),
    )
    assert "cursor.key.bind" in {c["key"] for c in me_before.json()}

    deny = client.post(
        "/api/assistant/v1/capabilities/assignments",
        json={
            "team_id": TEAM_ID,
            "scope_type": "user_deny",
            "scope_id": MEMBER_ID,
            "capability_key": "cursor.key.bind",
        },
        headers=_auth_headers(),
    )
    assert deny.status_code == 200
    assignment_id = deny.json()["id"]

    me_after = client.get(
        "/api/assistant/v1/capabilities/me",
        params={"team_id": TEAM_ID, "member_id": MEMBER_ID, "role": "ai_member"},
        headers=_auth_headers(),
    )
    keys = {c["key"] for c in me_after.json()}
    assert "cursor.key.bind" not in keys
    assert "quota.self.read" in keys

    deleted = client.delete(
        f"/api/assistant/v1/capabilities/assignments/{assignment_id}",
        headers=_auth_headers(),
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
