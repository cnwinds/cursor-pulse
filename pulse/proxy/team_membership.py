"""Per-team membership settings (TeamSetting section ``membership``)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import TeamSetting

MEMBERSHIP_SECTION = "membership"


def membership_required_for_team(session: Session, team_id: str) -> bool:
    row = session.scalar(
        select(TeamSetting).where(
            TeamSetting.team_id == team_id,
            TeamSetting.section == MEMBERSHIP_SECTION,
        )
    )
    if row is None or not isinstance(row.data, dict):
        return False
    return bool(row.data.get("membership_required"))


def set_membership_required(session: Session, team_id: str, required: bool) -> None:
    """测试与后续管理 API 写入入口。"""
    row = session.scalar(
        select(TeamSetting).where(
            TeamSetting.team_id == team_id,
            TeamSetting.section == MEMBERSHIP_SECTION,
        )
    )
    data = {"membership_required": required}
    if row is None:
        session.add(TeamSetting(team_id=team_id, section=MEMBERSHIP_SECTION, data=data))
    else:
        row.data = {**(row.data or {}), **data}
    session.flush()
