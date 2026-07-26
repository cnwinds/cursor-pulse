"""Team token usage analytics API (calendar range over daily aggregates)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pulse.settings.team_store import effective_config_dict
from pulse.tool_center.usage_analytics import (
    build_usage_analytics_daily_breakdown,
    build_usage_analytics_overview,
    parse_date_param,
    parse_overview_filters,
    validate_range,
)


def register_usage_analytics_routes(app, get_db, require_capability, team_repo_fn, config):
    @app.get(
        "/api/v2/usage-analytics/overview",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def usage_analytics_overview(
        start: str = Query(..., description="YYYY-MM-DD"),
        end: str = Query(..., description="YYYY-MM-DD"),
        account_ids: str | None = Query(default=None, description="逗号分隔账号 ID"),
        primary_member_ids: str | None = Query(default=None, description="逗号分隔主使用人 ID"),
        top_n: int = Query(default=10, ge=1, le=50),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        try:
            start_d, end_d, account_id_list, member_id_list = parse_overview_filters(
                start=start,
                end=end,
                account_ids=account_ids,
                primary_member_ids=primary_member_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        effective = effective_config_dict(config, session, team.id)
        timezone = str(
            (effective.get("collection") or {}).get("timezone") or config.collection.timezone
        )
        return build_usage_analytics_overview(
            session,
            team.id,
            start=start_d,
            end=end_d,
            timezone=timezone,
            account_ids=account_id_list,
            primary_member_ids=member_id_list,
            top_n=top_n,
        )

    @app.get(
        "/api/v2/usage-analytics/daily-breakdown",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def usage_analytics_daily_breakdown(
        start: str = Query(..., description="YYYY-MM-DD"),
        end: str = Query(..., description="YYYY-MM-DD"),
        account_id: str | None = Query(default=None),
        model: str | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        try:
            start_d = parse_date_param(start, field="start")
            end_d = parse_date_param(end, field="end")
            validate_range(start_d, end_d)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "items": build_usage_analytics_daily_breakdown(
                session,
                team.id,
                start=start_d,
                end=end_d,
                account_id=account_id,
                model=model,
            ),
        }
