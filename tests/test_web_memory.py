import pytest
from sqlalchemy import create_engine

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from personamem.db import init_memory_tables
from personamem.domain import AtomKind, MemoryAtom, Principle, PrincipleTier, SourceVisibility, Sensitivity
from personamem.repository import SqlAlchemyMemoryRepository
from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.memory_adapter.identity import team_id_to_namespace
from pulse.storage.models import Base, Member
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo
import uuid
from datetime import datetime, timezone


@pytest.fixture
def memory_client():
    config = AppConfig(
        web=WebConfig(admin_token="secret-token", jwt_secret="jwt-test-secret"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    init_memory_tables(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = session_factory()
    team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    member.portal_status = "pending"
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin1", display_name="Admin", password="pass1234"
    )
    namespace = team_id_to_namespace(team.id)
    mem_repo = SqlAlchemyMemoryRepository(session)
    now = datetime.now(timezone.utc)
    mem_repo.upsert_atom(
        MemoryAtom(
            id=str(uuid.uuid4()),
            namespace=namespace,
            subject_id=member.id,
            kind=AtomKind.FACT,
            content="Alice prefers Opus for refactoring",
            source_visibility=SourceVisibility.PRIVATE,
            sensitivity=Sensitivity.INTERNAL,
            confidence=0.9,
            created_at=now,
            last_seen_at=now,
        )
    )
    mem_repo.add_principle(
        Principle(
            id=str(uuid.uuid4()),
            namespace=namespace,
            tier=PrincipleTier.LEARNED,
            rule="催办语气要温和",
            status="active",
            created_at=now,
        )
    )
    repo.commit()
    session.close()
    app = create_app(config, session_factory)
    yield TestClient(app), config, owner, team.id


def test_memory_atoms(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    res = client.get("/api/memory/atoms", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert len(res.json()) >= 1
    assert "Alice" in res.json()[0]["subject_name"] or "Opus" in res.json()[0]["content"]


def test_memory_principles(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    res = client.get("/api/memory/principles", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert any("温和" in p["rule"] for p in res.json())


def test_create_principle(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    res = client.post(
        "/api/memory/principles",
        headers={"Authorization": f"Bearer {token}"},
        json={"rule": "新原则测试", "tier": "learned"},
    )
    assert res.status_code == 200
    assert res.json()["rule"] == "新原则测试"


def test_audit_logs(memory_client):
    client, config, owner, _ = memory_client
    token = create_access_token(config, owner)
    client.post(
        "/api/memory/principles",
        headers={"Authorization": f"Bearer {token}"},
        json={"rule": "审计测试", "tier": "learned"},
    )
    res = client.get("/api/audit-logs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert len(res.json()["admin_actions"]) >= 1


def test_portal_grant(memory_client):
    client, config, owner, _team_id = memory_client
    token = create_access_token(config, owner)
    pending = client.get(
        "/api/portal/users/pending",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    alice = next(u for u in pending if u["display_name"] == "Alice")
    res = client.post(
        f"/api/portal/users/{alice['id']}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"portal_role": "auditor"},
    )
    assert res.status_code == 200
    assert res.json()["portal_role"] == "auditor"
