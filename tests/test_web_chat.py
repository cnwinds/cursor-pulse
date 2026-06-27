import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from tests.conftest import make_team_repo


@pytest.fixture
def chat_client():
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
    from personamem.db import init_memory_tables

    init_memory_tables(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = session_factory()
    _team, repo = make_team_repo(session)
    owner = bootstrap_portal_owner(
        repo, dingtalk_user_id="admin1", display_name="Admin", password="pass1234"
    )
    repo.commit()
    session.close()
    app = create_app(config, session_factory)
    yield TestClient(app), config, owner


def test_chat_api(chat_client):
    client, config, owner = chat_client
    token = create_access_token(config, owner)
    res = client.post(
        "/api/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "你好"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "reply" in body
    assert isinstance(body["actions"], list)


def test_chat_requires_auth(chat_client):
    client, _, _ = chat_client
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
