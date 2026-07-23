"""Extract interaction profile signals from structured session summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.memory.contracts import ProfileDimension, SessionSummary, SessionSummaryItem
from assistant_platform.profiles.models import ProfileSignalRow

_SIGNAL_TTL = timedelta(days=90)
_CONTENT_MAX = 240

_EXPLICIT_MARKERS = (
  re.compile(r"^偏好[:：]", re.IGNORECASE),
  re.compile(r"请用", re.IGNORECASE),
  re.compile(r"我喜欢", re.IGNORECASE),
  re.compile(r"不要", re.IGNORECASE),
  re.compile(r"以后请", re.IGNORECASE),
)

_DIMENSION_RULES: tuple[tuple[ProfileDimension, re.Pattern[str]], ...] = (
    (ProfileDimension.LANGUAGE, re.compile(r"(英文|中文|English|Chinese)", re.IGNORECASE)),
    (ProfileDimension.VERBOSITY, re.compile(r"(简洁|简短|详细|啰嗦|长一点|短一点)", re.IGNORECASE)),
    (ProfileDimension.FORMALITY, re.compile(r"(正式|口语|随意|礼貌)", re.IGNORECASE)),
    (ProfileDimension.STRUCTURE, re.compile(r"(列表|分点|步骤|段落)", re.IGNORECASE)),
    (ProfileDimension.ADDRESSING, re.compile(r"(叫我|称呼|叫我为)", re.IGNORECASE)),
    (ProfileDimension.EXAMPLES, re.compile(r"(举例|例子|示例)", re.IGNORECASE)),
    (ProfileDimension.PROACTIVITY, re.compile(r"(主动|别主动|不用提醒)", re.IGNORECASE)),
    (ProfileDimension.CONFIRMATION, re.compile(r"(先确认|不用确认|问我一下)", re.IGNORECASE)),
    (ProfileDimension.EXPLICIT_TABOO, re.compile(r"(不要|禁止|别)", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ExtractedProfileSignal:
    dimension: ProfileDimension
    content: str
    confidence: float
    explicitness: str
    evidence_session_ids: tuple[str, ...]
    evidence_chunk_ids: tuple[str, ...]
    evidence_message_seqs: tuple[int, ...]


def _is_explicit(content: str) -> bool:
    return any(pattern.search(content) for pattern in _EXPLICIT_MARKERS)


def _infer_dimension(content: str) -> ProfileDimension:
    for dimension, pattern in _DIMENSION_RULES:
        if pattern.search(content):
            return dimension
    return ProfileDimension.VERBOSITY


def _truncate(content: str) -> str:
    text = content.strip()
    if len(text) <= _CONTENT_MAX:
        return text
    return text[:_CONTENT_MAX] + "…"


def _item_evidence(item: SessionSummaryItem) -> tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]:
    chunk_ids: list[str] = []
    seqs: list[int] = []
    for evidence in item.evidence:
        if evidence.chunk_id:
            chunk_ids.append(evidence.chunk_id)
        if evidence.message_seq is not None:
            seqs.append(evidence.message_seq)
    return (), tuple(chunk_ids), tuple(seqs)


def extract_signals_from_summary(
    summary: SessionSummary,
    *,
    user_id: str,
    team_id: str,
) -> list[ExtractedProfileSignal]:
    if summary.scope.value != "personal":
        return []

    extracted: list[ExtractedProfileSignal] = []
    for item in (*summary.preferences, *summary.facts):
        content = _truncate(item.content)
        if not content:
            continue
        explicit = _is_explicit(content) or item.kind == "preference"
        extracted.append(
            ExtractedProfileSignal(
                dimension=_infer_dimension(content),
                content=content,
                confidence=item.confidence if explicit else max(0.3, item.confidence - 0.2),
                explicitness="explicit" if explicit else "inferred",
                evidence_session_ids=(summary.session_id,),
                evidence_chunk_ids=_item_evidence(item)[1],
                evidence_message_seqs=_item_evidence(item)[2],
            )
        )
    return extracted


def persist_profile_signals(
    session: Session,
    *,
    user_id: str,
    team_id: str,
    session_id: str,
    signals: list[ExtractedProfileSignal],
) -> list[ProfileSignalRow]:
    if not signals:
        return []

    existing = session.scalars(
        select(ProfileSignalRow).where(
            ProfileSignalRow.user_id == user_id,
            ProfileSignalRow.team_id == team_id,
            ProfileSignalRow.status == "active",
        )
    ).all()
    existing_by_key = {
        (row.dimension or "verbosity", row.content.strip().lower()): row for row in existing
    }

    saved: list[ProfileSignalRow] = []
    expires_at = datetime.now(timezone.utc) + _SIGNAL_TTL
    for signal in signals:
        key = (signal.dimension.value, signal.content.strip().lower())
        prior = existing_by_key.get(key)
        if prior and session_id in (prior.source_session_ids_json or []):
            saved.append(prior)
            continue

        row = ProfileSignalRow(
            user_id=user_id,
            team_id=team_id,
            kind="preference",
            dimension=signal.dimension.value,
            content=signal.content,
            confidence=signal.confidence,
            explicitness=signal.explicitness,
            status="active",
            source_session_ids_json=[session_id],
            evidence_json={
                "session_ids": list(signal.evidence_session_ids),
                "chunk_ids": list(signal.evidence_chunk_ids),
                "message_seqs": list(signal.evidence_message_seqs),
            },
            expires_at=expires_at,
        )
        session.add(row)
        session.flush()
        saved.append(row)
        existing_by_key[key] = row
    return saved


def extract_profile_signals_from_session(
    session: Session,
    session_row: ChatSessionRow,
    summary: SessionSummary,
) -> list[ProfileSignalRow]:
    """Private chats only — group sessions must not infer personal profile."""
    if session_row.conversation_type == "group" or not session_row.user_id:
        return []
    signals = extract_signals_from_summary(
        summary,
        user_id=session_row.user_id,
        team_id=session_row.team_id,
    )
    return persist_profile_signals(
        session,
        user_id=session_row.user_id,
        team_id=session_row.team_id,
        session_id=session_row.id,
        signals=signals,
    )
