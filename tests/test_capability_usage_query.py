from __future__ import annotations

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.usage_query import handle_usage_query
from pulse.capabilities.invoke import HANDLERS
from pulse.config import AppConfig, CollectionConfig, CredentialConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_usage_query_registered():
    assert ("usage.query", "1") in HANDLERS


def test_handle_usage_query_requires_llm():
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
    )
    result = handle_usage_query(session, request=req, config=config, op={})
    assert result.status == "failed"
    assert result.error_code == "llm_unavailable"
    assert "大模型" in result.user_message
