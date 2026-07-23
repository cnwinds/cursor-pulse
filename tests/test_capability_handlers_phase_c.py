from __future__ import annotations

def _msg(result):
    data = result.result or {}
    return result.user_message or data.get("text") or data.get("answer") or ""


from unittest.mock import patch

from sqlalchemy import select

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.handlers.key_loan import handle_key_loan_request
from pulse.capabilities.handlers.text_capabilities import (
    handle_report_publish,
    handle_submission_self_read,
)
from pulse.capabilities.invoke import invoke_capability
from pulse.capabilities.routing_metrics import reset, snapshot
from pulse.config import AppConfig, TenantConfig
from pulse.storage.db import init_db
from pulse.storage.models import Team
from pulse.storage.repository import Repository


def _team_repo(session):
    team = session.scalar(select(Team).where(Team.slug == "test"))
    if team is None:
        team = Team(slug="test", name="Test")
        session.add(team)
        session.flush()
    return team, Repository(session, team.id)


def _request(*, member_id: str, team_id: str, capability_key: str, arguments: dict | None = None):
    return CapabilityInvokeRequest(
        invocation_id="inv-1",
        idempotency_key="idem-1",
        capability_key=capability_key,
        capability_version="1",
        team_id=team_id,
        actor_member_id=member_id,
        arguments=arguments or {},
        confirmed_by=member_id,
    )


def test_handle_submission_self_read_no_record():
    session = init_db("sqlite:///:memory:")()
    try:
        team, repo = _team_repo(session)
        member = repo.add_member("u1", "User")
        repo.commit()
        result = handle_submission_self_read(
            session,
            request=_request(
                member_id=member.id,
                team_id=team.id,
                capability_key="submission.self.read",
            ),
            config=AppConfig(tenant=TenantConfig(slug="t", name="T")),
            op={},
        )
        assert result.status == "succeeded"
        assert "暂无提交记录" in _msg(result)
    finally:
        session.close()


def test_handle_report_publish_owner_preview():
    session = init_db("sqlite:///:memory:")()
    try:
        team, repo = _team_repo(session)
        member = repo.add_member("owner-user", "Owner")
        member.portal_role = "owner"
        repo.commit()
        config = AppConfig(tenant=TenantConfig(slug="test", name="Test"))
        config.admin.dingtalk_user_ids = []
        with patch(
            "pulse.capabilities.handlers.text_capabilities.publish_report_to_group",
            return_value="月报正文",
        ):
            result = handle_report_publish(
                session,
                request=_request(
                    member_id=member.id,
                    team_id=team.id,
                    capability_key="report.publish",
                    arguments={"text": "报告 2026-07"},
                ),
                config=config,
                op={},
            )
        assert result.status == "succeeded"
        assert "暂未发群" in _msg(result)
        assert "月报正文" in _msg(result)
    finally:
        session.close()


def test_invoke_uses_dedicated_handler():
    session = init_db("sqlite:///:memory:")()
    try:
        team, repo = _team_repo(session)
        member = repo.add_member("u1", "User")
        repo.commit()
        reset()
        result = invoke_capability(
            session,
            request=_request(
                member_id=member.id,
                team_id=team.id,
                capability_key="submission.self.read",
            ),
            config=AppConfig(tenant=TenantConfig(slug="t", name="T")),
        )
        assert result.status == "succeeded"
        metrics = snapshot()
        assert metrics["dedicated_total"] == 1
        assert metrics["invoke_by_capability"]["submission.self.read"] == 1
    finally:
        session.close()


def test_invoke_records_missing_handler():
    session = init_db("sqlite:///:memory:")()
    try:
        team, repo = _team_repo(session)
        member = repo.add_member("u1", "User")
        repo.commit()
        reset()
        with patch.dict(
            "pulse.capabilities.invoke.HANDLERS",
            {},
            clear=True,
        ):
            result = invoke_capability(
                session,
                request=_request(
                    member_id=member.id,
                    team_id=team.id,
                    capability_key="submission.self.read",
                ),
                config=AppConfig(tenant=TenantConfig(slug="t", name="T")),
            )
        assert result.status == "failed"
        assert result.error_code == "handler_not_implemented"
        metrics = snapshot()
        assert metrics["missing_handler_by_capability"]["submission.self.read"] == 1
    finally:
        session.close()


def test_key_loan_request_structured_note():
    session = init_db("sqlite:///:memory:")()
    try:
        team, repo = _team_repo(session)
        member = repo.add_member("u1", "User")
        repo.commit()
        config = AppConfig(tenant=TenantConfig(slug="test", name="Test"))
        with patch(
            "pulse.capabilities.handlers.key_loan.request_loan_payload",
            return_value={
                "ok": True,
                "api_key": "crsr_x",
                "lender_name": "Admin",
                "warning": "ok",
            },
        ) as request_loan:
            result = handle_key_loan_request(
                session,
                request=_request(
                    member_id=member.id,
                    team_id=team.id,
                    capability_key="key.loan.request",
                    arguments={"note": "项目赶工"},
                ),
                config=config,
                op={},
            )
        assert result.status == "succeeded"
        assert result.user_message == ""
        assert (result.result or {}).get("api_key") == "crsr_x"
        request_loan.assert_called_once()
        assert request_loan.call_args.kwargs["note"] == "项目赶工"
    finally:
        session.close()
