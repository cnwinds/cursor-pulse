from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.evaluation.models import EvaluationRunRow
from assistant_platform.review.models import SessionReviewRow


def run_evaluation_stub(
    db_session: Session,
    release_id: str,
    limit: int = 5,
) -> EvaluationRunRow:
    closed_sessions = list(
        db_session.scalars(
            select(ChatSessionRow)
            .where(ChatSessionRow.status == "closed")
            .order_by(ChatSessionRow.closed_at.desc())
            .limit(limit)
        )
    )

    comparisons: list[dict] = []
    total_score = 0
    for session_row in closed_sessions:
        review = db_session.scalar(
            select(SessionReviewRow).where(SessionReviewRow.session_id == session_row.id)
        )
        baseline_score = review.score if review is not None else 80
        replay_score = max(0, min(100, baseline_score + 2))
        comparisons.append(
            {
                "session_id": session_row.id,
                "baseline_score": baseline_score,
                "replay_score": replay_score,
                "delta": replay_score - baseline_score,
            }
        )
        total_score += replay_score

    session_count = len(closed_sessions)
    average_score = round(total_score / session_count, 2) if session_count else 0.0

    run_row = EvaluationRunRow(
        release_id=release_id,
        status="completed",
        result_json={
            "session_count": session_count,
            "average_score": average_score,
            "comparisons": comparisons,
            "stub": True,
        },
    )
    db_session.add(run_row)
    db_session.flush()
    return run_row
