import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.storage.models import AiAccount, Base, KeyLoan, Member, Team
from pulse.web.audit import (
    _AuditContext,
    action_label,
    format_audit_detail,
    list_admin_audit_logs,
    log_admin_action,
)


@pytest.fixture
def audit_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _ctx(**kwargs) -> _AuditContext:
    return _AuditContext(**kwargs)


def test_action_label_known_and_chat_tool():
    assert action_label("credential.sync") == "同步用量"
    assert action_label("chat.tool.usage_self") == "对话工具 · usage_self"


def test_format_credential_sync():
    account_id = "296cdb63-dd31-4f62-b7ac-a7fa43ac0175"
    account = AiAccount(id=account_id, account_identifier="team@cursor.com", vendor_id="v", plan_id="p")
    ctx = _ctx(accounts={account_id: account})
    detail = format_audit_detail(
        "credential.sync",
        f"{account_id}:457 events",
        ctx,
    )
    assert detail == "同步 team@cursor.com，拉取 457 条用量事件"


def test_format_quota_loan_and_revoke():
    account_id = "998c93cd-cb7c-4911-985e-fe8fbc2ae62f"
    loan_id = "f82abe14-603d-453b-b4ed-9f57a8462c55"
    borrower_id = "4cec80bc-921e-4eb2-92fa-735ba575246a"
    account = AiAccount(id=account_id, account_identifier="shared-key", vendor_id="v", plan_id="p")
    borrower = Member(id=borrower_id, display_name="陆宗博", dingtalk_user_id="u1")
    loan = KeyLoan(
        id=loan_id,
        source_account_id=account_id,
        credential_id="cred",
        borrower_member_id=borrower_id,
    )
    ctx = _ctx(
        accounts={account_id: account},
        members_by_id={borrower_id: borrower},
        loans={loan_id: loan},
    )
    assert format_audit_detail(
        "quota.loan_key",
        f"{account_id}->陆宗博",
        ctx,
    ) == "将 shared-key 的密钥借给 陆宗博"
    assert format_audit_detail("quota.revoke_loan", loan_id, ctx) == (
        "收回 从 shared-key 借给 陆宗博"
    )


def test_format_portal_user_actions():
    member = Member(id="m1", display_name="陈新志", dingtalk_user_id="dt-001")
    ctx = _ctx(members_by_dingtalk={"dt-001": member})
    assert format_audit_detail("portal.user.approve", "dt-001 -> operator", ctx) == (
        "批准 陈新志，角色设为 运营员"
    )
    assert format_audit_detail("portal.user.disable", "dt-001", ctx) == "禁用门户用户 陈新志"


def test_list_admin_audit_logs_enriches_rows(audit_session):
    session = audit_session
    team = Team(slug="audit-team", name="Audit Team")
    session.add(team)
    session.flush()
    operator = Member(team_id=team.id, display_name="熊波", dingtalk_user_id="op1")
    session.add(operator)
    session.flush()
    account = AiAccount(
        team_id=team.id,
        vendor_id="vendor-1",
        plan_id="plan-1",
        account_identifier="ops@example.com",
    )
    session.add(account)
    session.flush()
    log_admin_action(
        session,
        team_id=team.id,
        member_id=operator.id,
        action="credential.sync",
        capability="accounts:write",
        detail=f"{account.id}:12 events",
    )
    session.commit()

    rows = list_admin_audit_logs(session, team.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["operator_name"] == "熊波"
    assert row["action_label"] == "同步用量"
    assert row["capability_label"] == "账号管理"
    assert row["channel_label"] == "网页后台"
    assert "ops@example.com" in row["detail"]
    assert "12 条用量事件" in row["detail"]
