from pathlib import Path

from pulse.report.formatter import format_monthly_report
from pulse.report.insights import generate_insights


def test_format_monthly_report_markdown_tables_no_personal_names():
    metrics = {
        "period": "2026-06",
        "total_events": 100,
        "total_tokens": 50000,
        "total_cost_usd": 12.5,
        "mom_events_change_pct": 25.0,
        "mom_tokens_change_pct": 10.0,
        "mom_cost_change_pct": -5.0,
        "account_count_ledger": 5,
        "account_count_submitted": 3,
        "account_count_unsubmitted": 2,
        "events_by_model": {"auto": 80, "composer-2.5": 20},
        "tokens_by_model": {"auto": 40000, "composer-2.5": 10000},
        "cost_by_model": {"auto": 10.0, "composer-2.5": 2.5},
        "member_names": {"m1": "Alice"},
        "events_by_member": [{"member_id": "m1", "value": 60, "rank": 1}],
        "unsubmitted_members": ["Carol"],
    }
    text = format_monthly_report(metrics)
    assert "## Cursor 用量月报 · 2026-06" in text
    assert "| 请求数 |" in text
    assert "| Tokens |" in text
    assert "+25.0%" in text
    assert "+10.0%" in text
    assert "### 模型用量" in text
    assert "| auto |" in text
    assert "### 台账参与" in text
    assert "| 本期无数据 | 2 |" in text
    assert "Alice" not in text
    assert "Carol" not in text
    assert "排名" not in text


def test_insights_no_personal_names():
    metrics = {
        "mom_events_change_pct": 30.0,
        "mom_tokens_change_pct": 15.0,
        "events_by_model": {"auto": 10},
        "account_count_unsubmitted": 2,
        "total_cost_usd": 0,
        "member_names": {"m1": "Alice"},
        "events_by_member": [{"member_id": "m1", "value": 10, "rank": 1}],
    }
    text = generate_insights(metrics)
    assert "30.0%" in text
    assert "auto" in text
    assert "2 个账号" in text
    assert "Alice" not in text


def test_publish_report_skips_group_when_disabled(tmp_path):
    from unittest.mock import MagicMock

    from pulse.aggregate.engine import aggregate_period
    from pulse.config import AppConfig, CollectionConfig, TenantConfig
    from pulse.extract.csv_parser import parse_usage_events_csv
    from pulse.report.service import publish_report_to_group, should_publish_report_to_group
    from pulse.storage.db import init_db
    from tests.conftest import make_team_repo

    sample = Path(__file__).resolve().parent / "fixtures" / "mini_usage_events.csv"
    db_url = f"sqlite:///{tmp_path / 'report.db'}"
    session = init_db(db_url)()
    team, repo = make_team_repo(session)
    member = repo.add_member("user-1", "Alice")
    repo.save_csv_ingestion(
        member=member,
        period="2026-07",
        parsed=parse_usage_events_csv(sample),
        submit_channel="private",
    )
    repo.commit()
    aggregate_period(session, "2026-07", team_id=team.id)
    session.commit()

    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        collection=CollectionConfig(publish_report_to_group=False),
    )
    assert should_publish_report_to_group(config) is False

    messenger = MagicMock()
    text = publish_report_to_group(
        session,
        "2026-07",
        messenger,
        team_id=team.id,
        reaggregate=False,
        config=config,
    )
    assert "2026-07" in text
    assert "| 请求数 |" in text
    messenger.send_group_text.assert_not_called()
    session.close()
