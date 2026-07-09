import pytest
from sqlalchemy import select

from pulse.storage.models import AiAccount, UsageIngestion
from pulse.storage.models import Member
from pulse.tool_center.account_pick import (
    filter_cursor_accounts,
    format_cursor_account_choice_prompt,
    looks_like_account_selection_cancel,
    parse_account_selection_text,
    parse_proxy_member_name,
    resolve_member_by_display_name,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.seed import seed_v2_catalog
from tests.conftest import make_team_repo


@pytest.fixture
def cursor_accounts_env(tmp_path):
    from pulse.storage.db import init_db

    db_url = f"sqlite:///{tmp_path / 'accounts.db'}"
    session_factory = init_db(db_url)
    session = session_factory()
    team, repo = make_team_repo(session)
    member = repo.add_member("u1", "Alice")
    seed_v2_catalog(session, team)
    session.flush()
    tool_repo = ToolCenterRepository(session, team.id)
    accounts = tool_repo.list_active_accounts()
    cursor_accounts = filter_cursor_accounts(accounts)
    for account in cursor_accounts[:2]:
        account.primary_member_id = member.id
    session.commit()
    yield session, repo, member, cursor_accounts[:2]
    session.close()


def test_parse_account_selection_by_index(cursor_accounts_env):
    _session, _repo, _member, accounts = cursor_accounts_env
    selected = parse_account_selection_text("2", accounts)
    assert selected is accounts[1]


def test_parse_account_selection_by_email(cursor_accounts_env):
    _session, _repo, _member, accounts = cursor_accounts_env
    selected = parse_account_selection_text(accounts[0].account_identifier, accounts)
    assert selected is accounts[0]


def test_parse_account_selection_cancel():
    assert looks_like_account_selection_cancel("取消") is True


def test_format_cursor_account_choice_prompt_lists_accounts(cursor_accounts_env):
    _session, _repo, _member, accounts = cursor_accounts_env
    prompt = format_cursor_account_choice_prompt(accounts)
    assert "多个 Cursor 账号" in prompt
    assert accounts[0].account_identifier in prompt


def test_save_submission_rejects_multiple_cursor_accounts_without_id(cursor_accounts_env):
    from pulse.extract.csv_parser import parse_usage_events_csv
    from pathlib import Path

    session, repo, member, _accounts = cursor_accounts_env
    sample = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"
    parsed = parse_usage_events_csv(sample)
    with pytest.raises(ValueError, match="多个 Cursor 账号"):
        repo.save_ingestion(
            member=member,
            period="2026-06",
            parsed=parsed,
            submit_channel="private",
        )


def test_parse_proxy_member_name_variants():
    assert parse_proxy_member_name("这个是帮 朱涛提交的") == "朱涛"
    assert parse_proxy_member_name("帮朱涛提交") == "朱涛"
    assert parse_proxy_member_name("代刘啸峰上报") == "刘啸峰"
    assert parse_proxy_member_name("1") is None


def test_resolve_member_by_display_name():
    members = [
        Member(display_name="朱涛"),
        Member(display_name="朱涛涛"),
    ]
    assert resolve_member_by_display_name(members, "朱涛") is members[0]
    assert resolve_member_by_display_name(members, "不存在") is None


def test_format_cursor_account_choice_prompt_admin_hint(cursor_accounts_env):
    _session, _repo, _member, accounts = cursor_accounts_env
    prompt = format_cursor_account_choice_prompt(accounts, admin_hint=True)
    assert "管理员" in prompt
    assert "帮" in prompt


def test_save_submission_admin_proxy(cursor_accounts_env):
    from pulse.extract.csv_parser import parse_usage_events_csv
    from pathlib import Path

    session, repo, member, accounts = cursor_accounts_env
    admin = repo.add_member("admin1", "Admin")
    target = repo.add_member("u2", "Bob")
    accounts[0].primary_member_id = target.id
    session.commit()
    sample = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"
    parsed = parse_usage_events_csv(sample)
    ingestion = repo.save_ingestion(
        member=admin,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
        account_id=accounts[0].id,
        allow_proxy=True,
    )
    repo.commit()
    assert ingestion.member_id == admin.id
    assert ingestion.account_id == accounts[0].id


def test_save_submission_rejects_proxy_without_flag(cursor_accounts_env):
    from pulse.extract.csv_parser import parse_usage_events_csv
    from pathlib import Path

    session, repo, member, accounts = cursor_accounts_env
    admin = repo.add_member("admin1", "Admin")
    target = repo.add_member("u2", "Bob")
    accounts[0].primary_member_id = target.id
    session.commit()
    sample = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"
    parsed = parse_usage_events_csv(sample)
    with pytest.raises(ValueError, match="仅账号主使用人"):
        repo.save_ingestion(
            member=admin,
            period="2026-06",
            parsed=parsed,
            submit_channel="private",
            account_id=accounts[0].id,
        )


def test_save_submission_with_explicit_account_id(cursor_accounts_env):
    from pulse.extract.csv_parser import parse_usage_events_csv
    from pathlib import Path

    session, repo, member, accounts = cursor_accounts_env
    sample = Path(__file__).resolve().parents[1] / "samples" / "usage-events-sample.csv"
    parsed = parse_usage_events_csv(sample)
    ingestion = repo.save_ingestion(
        member=member,
        period="2026-06",
        parsed=parsed,
        submit_channel="private",
        account_id=accounts[0].id,
    )
    repo.commit()
    saved = session.get(UsageIngestion, ingestion.id)
    assert saved is not None
    assert saved.account_id == accounts[0].id
