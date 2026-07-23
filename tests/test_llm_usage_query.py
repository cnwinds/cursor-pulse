from __future__ import annotations

from unittest.mock import MagicMock

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.usage_query import handle_usage_query
from pulse.config import AppConfig, AssistantLlmSettings, CollectionConfig, CredentialConfig
from pulse.query.llm_query import answer_usage_with_llm, build_usage_context
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_build_usage_context_scopes_non_admin():
    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    repo.commit()
    ctx = build_usage_context(
        session,
        "2026-07",
        is_admin=False,
        member_id=member.id,
        member_name=member.display_name,
    )
    assert ctx["scope"] == "self"
    assert ctx["empty"] is True
    session.close()


def test_answer_usage_with_llm_calls_client(tmp_path):
    from pathlib import Path

    from pulse.extract.csv_parser import parse_usage_events_csv

    SAMPLE = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"
    db_url = f"sqlite:///{tmp_path / 'llm-q.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    parsed = parse_usage_events_csv(SAMPLE)
    repo.save_csv_ingestion(
        member=member, period="2026-06", parsed=parsed, submit_channel="private"
    )
    repo.commit()
    config = AppConfig(
        credentials=CredentialConfig(encryption_key=""),
        collection=CollectionConfig(timezone="Asia/Shanghai", period_format="%Y-%m"),
    )
    client = MagicMock()
    client.complete.return_value = "Alice 本月 tokens 合计 1,234。"
    from unittest.mock import patch

    with patch("pulse.query.llm_query.current_period", return_value="2026-06"):
        reply = answer_usage_with_llm(
            session,
            question="我本月用了多少 tokens",
            config=config,
            member_name=member.display_name,
            member_id=member.id,
            is_admin=False,
            client=client,
        )
    assert "1,234" in reply
    client.complete.assert_called_once()
    session.close()


def test_handle_usage_query_with_llm_enabled():
    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    actor = repo.add_member("actor-user", "Actor")
    repo.commit()
    config = AppConfig(
        credentials=CredentialConfig(encryption_key=""),
        collection=CollectionConfig(timezone="Asia/Shanghai", period_format="%Y-%m"),
        assistant_llm=AssistantLlmSettings(
            enabled=True,
            api_key="test-key",
            model="test-model",
        ),
    )
    req = CapabilityInvokeRequest(
        invocation_id="inv-u2",
        idempotency_key="idem-u2",
        team_id=team.id,
        actor_member_id=actor.id,
        capability_key="usage.query",
        capability_version="1",
        arguments={"text": "查询 我本月 tokens"},
    )
    from unittest.mock import patch

    with patch(
        "pulse.capabilities.handlers.usage_query.answer_usage_with_llm",
        return_value="你的 tokens 合计 100。",
    ):
        result = handle_usage_query(session, request=req, config=config, op={})
    assert result.status == "succeeded"
    assert result.result is not None
    assert "tokens" in str(result.result.get("answer", "")).lower()
    session.close()
