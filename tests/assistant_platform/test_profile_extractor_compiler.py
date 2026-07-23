from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from assistant_platform.memory.contracts import (
    MemoryScope,
    ProfileDimension,
    SessionSummary,
    SessionSummaryItem,
)
from assistant_platform.profiles.compiler import compile_profile_guidance
from assistant_platform.profiles.extractor import extract_signals_from_summary
from assistant_platform.profiles.models import ProfileCorrectionRow, ProfileSignalRow
from assistant_platform.storage.db import init_assistant_db

TEAM_ID = "team-profile"


def _summary(*, session_id: str, preference: str) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        scope=MemoryScope.PERSONAL,
        subject_id="u1",
        team_id=TEAM_ID,
        preferences=(
            SessionSummaryItem(
                content=preference,
                kind="preference",
                confidence=0.85,
                evidence=(),
            ),
        ),
    )


def test_extractor_marks_explicit_preferences():
    signals = extract_signals_from_summary(
        _summary(session_id="s1", preference="偏好: 简洁列表"),
        user_id="u1",
        team_id=TEAM_ID,
    )
    assert len(signals) == 1
    assert signals[0].explicitness == "explicit"
    assert signals[0].dimension in (ProfileDimension.STRUCTURE, ProfileDimension.VERBOSITY)


def test_compiler_prefers_correction_over_inferred_signal():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    signal = ProfileSignalRow(
        user_id="u1",
        team_id=TEAM_ID,
        kind="preference",
        dimension=ProfileDimension.VERBOSITY.value,
        content="喜欢详细回复",
        confidence=0.4,
        explicitness="inferred",
        status="active",
        source_session_ids_json=["s-old"],
    )
    session.add(signal)
    session.flush()
    session.add(
        ProfileCorrectionRow(
            user_id="u1",
            team_id=TEAM_ID,
            signal_id=signal.id,
            dimension=ProfileDimension.VERBOSITY.value,
            correction_text="请保持简洁",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    guidance = compile_profile_guidance(session, user_id="u1", team_id=TEAM_ID)
    assert len(guidance.items) == 1
    assert guidance.items[0].guidance == "请保持简洁"
    assert guidance.items[0].explicit is True
    session.close()


def test_compiler_picks_higher_explicitness_on_conflict():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    session.add(
        ProfileSignalRow(
            user_id="u1",
            team_id=TEAM_ID,
            kind="preference",
            dimension=ProfileDimension.LANGUAGE.value,
            content="用中文",
            confidence=0.5,
            explicitness="inferred",
            status="active",
            source_session_ids_json=["s1"],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    session.add(
        ProfileSignalRow(
            user_id="u1",
            team_id=TEAM_ID,
            kind="preference",
            dimension=ProfileDimension.LANGUAGE.value,
            content="请用英文回复",
            confidence=0.7,
            explicitness="explicit",
            status="active",
            source_session_ids_json=["s2"],
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
    )
    session.commit()

    guidance = compile_profile_guidance(session, user_id="u1", team_id=TEAM_ID)
    language = next(item for item in guidance.items if item.dimension == ProfileDimension.LANGUAGE)
    assert "英文" in language.guidance
    session.close()
