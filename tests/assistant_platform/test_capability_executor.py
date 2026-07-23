from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from assistant_platform.capabilities.executor import CapabilityExecutor
from assistant_platform.capabilities.models import ToolInvocationRow
from assistant_platform.capabilities.pulse_client import PulseCapabilityClient
from assistant_platform.config import AssistantConfig
from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from assistant_platform.storage.db import init_assistant_db

TEAM_ID = "team-executor"
MEMBER_ID = "member-executor"
SECRET_KEY = "test-assistant-secret-key"
PULSE_TOKEN = "pulse-internal-test-token"


@pytest.fixture
def assistant_config() -> AssistantConfig:
    return AssistantConfig(
        team_id=TEAM_ID,
        secret_key=SECRET_KEY,
        pulse_base_url="http://pulse.test",
        pulse_internal_token=PULSE_TOKEN,
    )


@pytest.fixture
def session():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def _executor(session, config: AssistantConfig, pulse_client: PulseCapabilityClient) -> CapabilityExecutor:
    return CapabilityExecutor(session=session, config=config, pulse_client=pulse_client)


def test_unauthorized_capability_forbidden_without_pulse_call(session, assistant_config):
    pulse_client = MagicMock(spec=PulseCapabilityClient)
    executor = _executor(session, assistant_config, pulse_client)

    result = executor.invoke(
        actor_member_id=MEMBER_ID,
        team_id=TEAM_ID,
        role="ai_member",
        capability_key="guide_image.update",
        arguments={},
        confirmed=True,
    )

    assert result.status == "failed"
    assert result.error_code == "forbidden"
    pulse_client.invoke.assert_not_called()

    rows = session.scalars(select(ToolInvocationRow)).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error_code == "forbidden"


def test_sensitive_without_confirmed_returns_confirmation_required(session, assistant_config):
    pulse_client = MagicMock(spec=PulseCapabilityClient)
    executor = _executor(session, assistant_config, pulse_client)

    result = executor.invoke(
        actor_member_id=MEMBER_ID,
        team_id=TEAM_ID,
        role="ai_member",
        capability_key="cursor.key.bind",
        arguments={"api_key": "crsr_test_secret_key_abcdefghijklmnop"},
        confirmed=False,
    )

    assert result.status == "failed"
    assert result.error_code == "confirmation_required"
    pulse_client.invoke.assert_not_called()

    row = session.scalar(select(ToolInvocationRow))
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == "confirmation_required"
    assert "api_key" not in (row.request_redacted_json or {})
    assert "crsr_" not in str(row.request_redacted_json)


def test_authorized_invoke_success_stores_invocation_and_calls_pulse(session, assistant_config):
    pulse_client = MagicMock(spec=PulseCapabilityClient)
    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="额度查询成功",
        result={"quota": {"remaining_cents": 5000}},
    )
    executor = _executor(session, assistant_config, pulse_client)

    result = executor.invoke(
        actor_member_id=MEMBER_ID,
        team_id=TEAM_ID,
        role="ai_member",
        capability_key="quota.self.read",
        arguments={},
        confirmed=False,
    )

    assert result.status == "succeeded"
    assert result.user_message == "额度查询成功"
    pulse_client.invoke.assert_called_once()
    request: CapabilityInvokeRequest = pulse_client.invoke.call_args.args[0]
    assert request.capability_key == "quota.self.read"
    assert request.team_id == TEAM_ID
    assert request.actor_member_id == MEMBER_ID

    row = session.scalar(select(ToolInvocationRow))
    assert row is not None
    assert row.status == "succeeded"
    assert row.capability_key == "quota.self.read"
    assert row.result_redacted_json == {"quota": {"remaining_cents": 5000}}
    assert row.error_code is None


def test_bind_stores_secret_ref_and_passes_api_key_to_pulse(session, assistant_config):
    api_key = "crsr_bind_test_key_abcdefghijklmnopqrst"
    pulse_client = MagicMock(spec=PulseCapabilityClient)
    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="绑定成功",
        result={"account_id": "acc-1"},
    )
    executor = _executor(session, assistant_config, pulse_client)

    result = executor.invoke(
        actor_member_id=MEMBER_ID,
        team_id=TEAM_ID,
        role="ai_member",
        capability_key="cursor.key.bind",
        arguments={"api_key": api_key, "email": "user@example.com"},
        confirmed=True,
    )

    assert result.status == "succeeded"
    pulse_client.invoke.assert_called_once()
    request: CapabilityInvokeRequest = pulse_client.invoke.call_args.args[0]
    assert request.arguments.get("api_key") == api_key
    assert request.confirmed_by == MEMBER_ID

    row = session.scalar(select(ToolInvocationRow))
    assert row is not None
    stored_args = row.request_redacted_json
    assert stored_args.get("email") == "user@example.com"
    assert "api_key" not in stored_args
    assert "secret_ref" in stored_args
    assert api_key not in str(stored_args)
