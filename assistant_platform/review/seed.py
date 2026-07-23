from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.review.models import ReviewRubricRow

_DEFAULT_RUBRIC = {
    "name": "default-heuristic",
    "criteria_json": {
        "base_score": 80,
        "penalties": {
            "error_messages": 20,
            "empty_assistant_reply": 10,
            "tool_failed": 15,
        },
        "human_queue_threshold": 60,
    },
}


def seed_review_rubrics(session: Session) -> None:
    existing = session.scalar(
        select(ReviewRubricRow).where(ReviewRubricRow.name == _DEFAULT_RUBRIC["name"])
    )
    if existing is not None:
        return
    session.add(ReviewRubricRow(**_DEFAULT_RUBRIC))
    session.flush()
