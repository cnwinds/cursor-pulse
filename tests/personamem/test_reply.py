from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from personamem.domain import (
    AtomKind,
    Commitment,
    CommitmentType,
    DeflectionReason,
    MemoryAtom,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
)
from personamem.engine import MemoryEngine
from personamem.models import Base
from personamem.persona import Persona
from personamem.repository import SqlAlchemyMemoryRepository
from personamem.responders import RuleBasedResponder
from pulse.memory_adapter.llm import RuleBasedDistiller, RuleBasedReviewer


@pytest.fixture
def engine_setup():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    repo = SqlAlchemyMemoryRepository(session)
    mem = MemoryEngine(
        repo=repo,
        distiller=RuleBasedDistiller(),
        reviewer=RuleBasedReviewer(),
        responder=RuleBasedResponder(),
        persona=Persona(name="小脉"),
    )
    mem.ensure_seeded("team-1")
    yield mem, session
    session.close()


def test_reply_uses_released_memory(engine_setup):
    mem, session = engine_setup
    now = datetime.now(timezone.utc)
    mem._repo.upsert_atom(
        MemoryAtom(
            id="a1",
            namespace="team-1",
            subject_id="u1",
            kind=AtomKind.PREFERENCE,
            content="偏好私聊提交 CSV",
            source_visibility=SourceVisibility.PRIVATE,
            sensitivity=Sensitivity.CONFIDENTIAL,
            confidence=1.0,
            created_at=now,
            last_seen_at=now,
        )
    )
    session.commit()

    text = mem.reply(
        namespace="team-1",
        subject_ids=["u1"],
        context=VisibilityContext.private("u1"),
        user_message="怎么提交用量",
        display_name="小王",
        is_group=False,
    )
    assert "偏好私聊" in text or "CSV" in text
    session.close()


def test_reply_deflects_in_group_with_commitment(engine_setup):
    mem, session = engine_setup
    now = datetime.now(timezone.utc)
    mem._repo.add_commitment(
        Commitment(
            id="c1",
            namespace="team-1",
            counterparty_id="u1",
            type=CommitmentType.PROMISED,
            statement="不说 Opus",
            scope={"topic_keywords": ["Opus"]},
            status="active",
            created_at=now,
        )
    )
    mem._repo.upsert_atom(
        MemoryAtom(
            id="a1",
            namespace="team-1",
            subject_id="u1",
            kind=AtomKind.FACT,
            content="小王 Opus 用量很高",
            source_visibility=SourceVisibility.PUBLIC,
            sensitivity=Sensitivity.PUBLIC,
            confidence=1.0,
            created_at=now,
            last_seen_at=now,
        )
    )
    session.commit()

    disclosure = mem.recall(
        namespace="team-1",
        subject_ids=["u1"],
        context=VisibilityContext.public(),
        query="谁 Opus 最多",
    )
    assert disclosure.deflection_reason == DeflectionReason.COMMITMENT

    text = mem.reply(
        namespace="team-1",
        subject_ids=["u1"],
        context=VisibilityContext.public(),
        user_message="谁 Opus 最多",
        display_name="同事",
        is_group=True,
        disclosure=disclosure,
    )
    assert "私聊" in text
    session.close()
