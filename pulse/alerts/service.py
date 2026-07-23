from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from pulse.alerts.engine import detect_anomalies
from pulse.aggregate.engine import _previous_period
from pulse.config import AppConfig
from pulse.report.service import get_latest_snapshot
from pulse.storage.models import AlertLog

logger = logging.getLogger(__name__)


def run_anomaly_check(
    session: Session,
    config: AppConfig,
    team_id: str,
    period: str,
    *,
    notify_admins=None,
) -> list[AlertLog]:
    if not config.alerts.enabled:
        return []

    current_snap = get_latest_snapshot(session, period, team_id=team_id)
    if not current_snap:
        logger.warning("No snapshot for anomaly check: %s", period)
        return []

    current = current_snap.metrics_json
    prev_period = _previous_period(period)
    prev_snap = get_latest_snapshot(session, prev_period, team_id=team_id)
    previous = prev_snap.metrics_json if prev_snap else None

    alerts = detect_anomalies(
        current,
        previous,
        member_events_spike_pct=config.alerts.member_events_spike_pct,
        team_events_spike_pct=config.alerts.team_events_spike_pct,
        member_cost_spike_usd=config.alerts.member_cost_spike_usd,
    )

    saved: list[AlertLog] = []
    for alert in alerts:
        row = AlertLog(
            team_id=team_id,
            period=period,
            alert_type=alert.alert_type,
            severity=alert.severity,
            member_id=alert.member_id,
            message=alert.message,
            details_json=alert.details,
        )
        session.add(row)
        saved.append(row)
    session.flush()

    if saved and notify_admins and config.admin.dingtalk_user_ids:
        lines = [f"⚠️ Cursor Pulse 异常告警 · {period}", ""]
        for row in saved[:10]:
            prefix = "🔴" if row.severity == "critical" else "🟡"
            lines.append(f"{prefix} {row.message}")
        text = "\n".join(lines)
        for admin_id in config.admin.dingtalk_user_ids:
            try:
                notify_admins(admin_id, text)
            except Exception:
                logger.exception("Failed to notify admin %s", admin_id)
        now = datetime.now(timezone.utc)
        for row in saved:
            row.sent_at = now

    return saved
