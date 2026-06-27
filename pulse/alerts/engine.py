from __future__ import annotations

from pulse.alerts.models import Alert


def detect_anomalies(
    current: dict,
    previous: dict | None,
    *,
    member_events_spike_pct: float = 100.0,
    team_events_spike_pct: float = 50.0,
    member_cost_spike_usd: float = 10.0,
) -> list[Alert]:
    """对比两期 snapshot，检测用量/花费突增（纯规则，可审计）。"""
    alerts: list[Alert] = []
    period = current.get("period", "?")
    names = current.get("member_names") or {}

    if previous:
        prev_events = previous.get("total_events") or 0
        curr_events = current.get("total_events") or 0
        if prev_events > 0:
            team_change = (curr_events - prev_events) / prev_events * 100
            if team_change >= team_events_spike_pct:
                alerts.append(
                    Alert(
                        alert_type="team_events_spike",
                        severity="warning",
                        message=(
                            f"{period} 团队总请求数环比 +{team_change:.1f}%"
                            f"（{prev_events:,} → {curr_events:,}）"
                        ),
                        details={
                            "prev_events": prev_events,
                            "curr_events": curr_events,
                            "change_pct": round(team_change, 2),
                        },
                    )
                )

        prev_by_member = {r["member_id"]: r for r in previous.get("events_by_member") or []}
        for row in current.get("events_by_member") or []:
            mid = row["member_id"]
            curr_val = float(row["value"])
            prev_row = prev_by_member.get(mid)
            if not prev_row:
                continue
            prev_val = float(prev_row["value"])
            if prev_val <= 0:
                continue
            change = (curr_val - prev_val) / prev_val * 100
            if change >= member_events_spike_pct:
                name = names.get(mid, mid[:8])
                alerts.append(
                    Alert(
                        alert_type="member_events_spike",
                        severity="warning",
                        member_id=mid,
                        message=f"{name} 请求数环比 +{change:.1f}%（{int(prev_val):,} → {int(curr_val):,}）",
                        details={
                            "member_id": mid,
                            "prev_events": prev_val,
                            "curr_events": curr_val,
                            "change_pct": round(change, 2),
                        },
                    )
                )

        prev_cost_rows = {r["member_id"]: r for r in previous.get("cost_by_member") or []}
        for row in current.get("cost_by_member") or []:
            mid = row["member_id"]
            curr_cost = float(row["value"])
            prev_cost = float(prev_cost_rows.get(mid, {}).get("value", 0))
            delta = curr_cost - prev_cost
            if delta >= member_cost_spike_usd:
                name = names.get(mid, mid[:8])
                alerts.append(
                    Alert(
                        alert_type="member_cost_spike",
                        severity="critical",
                        member_id=mid,
                        message=f"{name} 付费增加 ${delta:.2f}（${prev_cost:.2f} → ${curr_cost:.2f}）",
                        details={
                            "member_id": mid,
                            "prev_cost_usd": prev_cost,
                            "curr_cost_usd": curr_cost,
                            "delta_usd": round(delta, 4),
                        },
                    )
                )

    unsubmitted = current.get("unsubmitted_members") or []
    if unsubmitted:
        alerts.append(
            Alert(
                alert_type="unsubmitted_members",
                severity="warning",
                message=f"{period} 仍有 {len(unsubmitted)} 人未提交：{'、'.join(unsubmitted[:5])}",
                details={"unsubmitted": unsubmitted},
            )
        )

    return alerts
