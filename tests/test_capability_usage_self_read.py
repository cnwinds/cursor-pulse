from __future__ import annotations

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.usage_self_read import handle_usage_self_read
from pulse.capabilities.invoke import HANDLERS
from pulse.config import AppConfig, CollectionConfig, CredentialConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_usage_self_read_registered():
    assert ("usage.self.read", "1") in HANDLERS


def test_handle_usage_self_read_no_accounts():
    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    actor = repo.add_member("actor-user", "Actor")
    repo.commit()
    config = AppConfig(
        credentials=CredentialConfig(encryption_key=""),
        collection=CollectionConfig(timezone="Asia/Shanghai", period_format="%Y-%m"),
    )
    req = CapabilityInvokeRequest(
        invocation_id="inv-s1",
        idempotency_key="idem-s1",
        team_id=team.id,
        actor_member_id=actor.id,
        capability_key="usage.self.read",
        capability_version="1",
        arguments={"text": "我的用量"},
    )
    result = handle_usage_self_read(session, request=req, config=config, op={})
    assert result.status == "succeeded"
    assert result.user_message == ""
    assert isinstance(result.result, dict)
    assert result.result.get("schema_version") == 1
    assert result.result.get("empty_reason") == "no_cursor_or_loan"
    assert result.result.get("accounts") == []
    assert "mode" in (result.result.get("query") or {})
    session.close()
