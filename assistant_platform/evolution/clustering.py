from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.evolution.models import FailureClusterRow, PromptChangeProposalRow
from assistant_platform.review.models import SessionReviewRow

_LOW_SCORE_THRESHOLD = 60

_TAG_DIFF_HINTS: dict[str, str] = {
    "error_messages": (
        "--- precepts.md\n+++ precepts.md\n"
        "@@ 错误处理\n"
        "+遇到上游错误时，简要说明原因并给出可执行的下一步建议。\n"
    ),
    "empty_assistant_reply": (
        "--- heart.md\n+++ heart.md\n"
        "@@ 回复完整性\n"
        "+即使用户问题简单，也需给出明确、非空的回复。\n"
    ),
    "tool_failed": (
        "--- precepts.md\n+++ precepts.md\n"
        "@@ 工具失败\n"
        "+工具调用失败时，向用户说明失败原因并建议重试或替代方案。\n"
    ),
}


def _suggest_diff_text(tag: str, session_ids: list[str]) -> str:
    hint = _TAG_DIFF_HINTS.get(
        tag,
        f"--- precepts.md\n+++ precepts.md\n@@ {tag}\n+根据失败标签 {tag} 优化回复策略。\n",
    )
    return (
        f"# Suggested prompt diff for failure tag: {tag}\n"
        f"# Sessions: {len(session_ids)}\n"
        f"{hint}"
    )


def cluster_low_score_reviews(db_session: Session) -> list[FailureClusterRow]:
    """Group low-score reviews by failure tag and create draft proposals.

    NEVER auto-applies changes to production — proposals stay in ``draft`` until
    a human approves and follows the canary → promote path.
    """
    reviews = list(
        db_session.scalars(
            select(SessionReviewRow).where(SessionReviewRow.score < _LOW_SCORE_THRESHOLD)
        )
    )

    tag_sessions: dict[str, set[str]] = {}
    for review in reviews:
        for tag in review.failure_tags_json or []:
            tag_sessions.setdefault(str(tag), set()).add(review.session_id)

    clusters: list[FailureClusterRow] = []
    for tag, session_ids_set in tag_sessions.items():
        session_ids = sorted(session_ids_set)
        cluster = db_session.scalar(
            select(FailureClusterRow).where(FailureClusterRow.tag == tag)
        )
        if cluster is None:
            cluster = FailureClusterRow(
                tag=tag,
                session_ids_json=session_ids,
                size=len(session_ids),
            )
            db_session.add(cluster)
            db_session.flush()
        else:
            merged = sorted(set(cluster.session_ids_json or []) | session_ids_set)
            cluster.session_ids_json = merged
            cluster.size = len(merged)
            db_session.add(cluster)
            db_session.flush()
        clusters.append(cluster)

        existing_draft = db_session.scalar(
            select(PromptChangeProposalRow).where(
                PromptChangeProposalRow.cluster_id == cluster.id,
                PromptChangeProposalRow.status == "draft",
            )
        )
        if existing_draft is None:
            db_session.add(
                PromptChangeProposalRow(
                    cluster_id=cluster.id,
                    diff_text=_suggest_diff_text(tag, cluster.session_ids_json or []),
                    status="draft",
                )
            )
            db_session.flush()

    return clusters
