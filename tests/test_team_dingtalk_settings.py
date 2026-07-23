import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi")
from pulse.config import AppConfig, TenantConfig, WebConfig, apply_team_dingtalk_overrides
from pulse.storage.models import Base
from pulse.web.app import create_app
from pulse.web.auth_tokens import create_access_token
from pulse.web.portal import bootstrap_portal_owner
from pulse.web.settings_store import patch_team_setting
from tests.conftest import make_team_repo


@pytest.fixture
def dingtalk_settings_client(tmp_path):
    config = AppConfig(
        web=WebConfig(admin_token="t", jwt_secret="jwt-test"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    db_path = tmp_path / "pulse.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    config.storage.database_url = db_url
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    s = sf()
    team, repo = make_team_repo(s)
    owner = bootstrap_portal_owner(repo, dingtalk_user_id="a1", display_name="A", password="x")
    repo.commit()
    s.close()
    return TestClient(create_app(config, sf)), config, owner, team.id, sf


def test_settings_includes_dingtalk(dingtalk_settings_client):
    client, _config, owner, _team_id, _sf = dingtalk_settings_client
    token = create_access_token(_config, owner)
    res = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert "dingtalk" in body
    assert "app_key" in body["dingtalk"]


def test_patch_dingtalk_settings_masks_secret(dingtalk_settings_client):
    client, config, owner, _team_id, _sf = dingtalk_settings_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.patch(
        "/api/settings/dingtalk",
        headers=headers,
        json={
            "data": {
                "app_key": "ding-key",
                "app_secret": "ding-secret",
                "robot_code": "robot-1",
                "group_open_conversation_id": "cid123==",
                "chat_id": "000000000000",
                "sync_root_dept_id": 1,
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dingtalk"]["app_key"] == "ding-key"
    assert body["dingtalk"]["app_secret"] == "***"
    assert body["dingtalk"]["robot_code"] == "robot-1"
    assert body["dingtalk"]["group_open_conversation_id"] == "cid123=="

    int_res = client.get("/api/system/integrations", headers=headers)
    assert int_res.status_code == 200
    intg = int_res.json()["dingtalk"]
    assert intg["app_configured"] is True
    assert intg["group_configured"] is True
    assert intg["robot_code"] is True


def test_reveal_dingtalk_app_secret(dingtalk_settings_client):
    client, config, owner, _team_id, _sf = dingtalk_settings_client
    token = create_access_token(config, owner)
    headers = {"Authorization": f"Bearer {token}"}
    client.patch(
        "/api/settings/dingtalk",
        headers=headers,
        json={"data": {"app_key": "ding-key", "app_secret": "ding-secret-value"}},
    )

    res = client.get("/api/settings/dingtalk/reveal/app_secret", headers=headers)
    assert res.status_code == 200
    assert res.json()["value"] == "ding-secret-value"

    bad = client.get("/api/settings/dingtalk/reveal/app_key", headers=headers)
    assert bad.status_code == 404


def test_apply_team_dingtalk_overrides_merges_db(dingtalk_settings_client):
    _client, config, owner, team_id, sf = dingtalk_settings_client
    session = sf()
    patch_team_setting(
        session,
        team_id=team_id,
        section="dingtalk",
        patch={
            "app_key": "db-key",
            "app_secret": "db-secret",
            "group_open_conversation_id": "cid-db==",
        },
        member_id=owner.id,
    )
    session.commit()
    session.close()

    merged = apply_team_dingtalk_overrides(config)
    assert merged.dingtalk.app_key == "db-key"
    assert merged.dingtalk.app_secret == "db-secret"
    assert merged.dingtalk.group_open_conversation_id == "cid-db=="
