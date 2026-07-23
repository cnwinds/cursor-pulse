from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.aggregate.engine import aggregate_period
from pulse.integrations.webhook import build_bi_payload, push_webhook
from pulse.llm.client import build_llm_client
from pulse.report.formatter import format_monthly_report
from pulse.report.narrative import generate_insights_with_fallback
from pulse.storage.models import MetricSnapshot, Report, Team

logger = logging.getLogger(__name__)


def get_latest_snapshot(
    session: Session,
    period: str,
    *,
    team_id: str | None = None,
) -> MetricSnapshot | None:
    query = select(MetricSnapshot).where(MetricSnapshot.period == period)
    if team_id:
        query = query.where(MetricSnapshot.team_id == team_id)
    return session.scalar(query.order_by(MetricSnapshot.computed_at.desc()))


def build_report_text(metrics: dict, config=None) -> tuple[str, str]:
    """生成月报正文。返回 (正文, insights_source)，source 为 llm 或 rules。"""
    facts = format_monthly_report(metrics)
    client = build_llm_client(config) if config else None
    insights, source = generate_insights_with_fallback(metrics, client)
    return facts + "\n\n" + insights, source


def generate_report(
    session: Session,
    period: str,
    *,
    team_id: str | None = None,
    reaggregate: bool = True,
    config=None,
) -> tuple[str, dict, Report | None]:
    """聚合（可选）并生成报告正文。返回 (正文, metrics, report)。"""
    if reaggregate:
        metrics = aggregate_period(session, period, team_id=team_id)
    else:
        snap = get_latest_snapshot(session, period, team_id=team_id)
        if not snap:
            raise ValueError(f"账期 {period} 无聚合快照，请先提交数据或运行聚合")
        metrics = snap.metrics_json
    text, _source = build_report_text(metrics, config)
    snap = get_latest_snapshot(session, period, team_id=team_id)
    report = Report(
        team_id=team_id,
        period=period,
        snapshot_id=snap.id if snap else None,
        narrative=text,
        posted_at=None,
    )
    session.add(report)
    session.flush()
    return text, metrics, report


def _push_bi_if_configured(session: Session, config, team_id: str, period: str, metrics: dict) -> None:
    integrations = config.integrations
    if not integrations.webhook_url:
        return
    if not integrations.push_on_report:
        return
    team = session.get(Team, team_id)
    payload = build_bi_payload(
        team_slug=team.slug if team else config.tenant.slug,
        team_name=team.name if team else config.tenant.name,
        period=period,
        metrics=metrics,
    )
    try:
        push_webhook(integrations.webhook_url, payload, secret=integrations.webhook_secret)
    except Exception:
        logger.exception("BI webhook push failed")


def should_publish_report_to_group(config) -> bool:
    if config is None:
        return False
    collection = getattr(config, "collection", None)
    if collection is None:
        return False
    return bool(getattr(collection, "publish_report_to_group", False))


def publish_report_to_group(
    session: Session,
    period: str,
    messenger,
    *,
    team_id: str | None = None,
    reaggregate: bool = True,
    config=None,
) -> str:
    text, metrics, report = generate_report(
        session,
        period,
        team_id=team_id,
        reaggregate=reaggregate,
        config=config,
    )
    if should_publish_report_to_group(config):
        messenger.send_group_text(text)
        if report:
            report.posted_at = datetime.now(timezone.utc)
    else:
        logger.info(
            "月报 %s 已生成，publish_report_to_group=false，跳过群发",
            period,
        )
    if config and team_id:
        _push_bi_if_configured(session, config, team_id, period, metrics)
    return text
