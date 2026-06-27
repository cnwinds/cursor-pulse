from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from personamem.domain import AtomKind, MemoryAtom, Sensitivity, SourceVisibility, VisibilityContext
from personamem.engine import MemoryEngine
from personamem.models import Base
from personamem.repository import SqlAlchemyMemoryRepository
from pulse.memory_adapter.llm import RuleBasedDistiller, RuleBasedReviewer


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


def test_distill_and_recall_private(engine):
    session = sessionmaker(bind=engine)()
    repo = SqlAlchemyMemoryRepository(session)
    mem = MemoryEngine(repo=repo, distiller=RuleBasedDistiller(), reviewer=RuleBasedReviewer())

    mem.distill(
        namespace="team-1",
        subject_id="u_wang",
        context=VisibilityContext.private("u_wang"),
        transcript="事实: 小王本月 Opus 用量偏高",
    )
    session.commit()

    disclosure = mem.recall(
        namespace="team-1",
        subject_ids=["u_wang"],
        context=VisibilityContext.private("u_wang"),
        query="Opus",
    )
    assert len(disclosure.released_atoms) == 1
    assert disclosure.released_atoms[0].kind == AtomKind.FACT

    public = mem.recall(
        namespace="team-1",
        subject_ids=["u_wang"],
        context=VisibilityContext.public(),
        query="Opus",
    )
    assert public.released_atoms == []
    assert public.deflection_reason.value == "privacy_default"
    session.close()


def test_commitment_blocks_public_recall(engine):
    session = sessionmaker(bind=engine)()
    repo = SqlAlchemyMemoryRepository(session)
    mem = MemoryEngine(repo=repo, distiller=RuleBasedDistiller(), reviewer=RuleBasedReviewer())

    mem.distill(
        namespace="team-1",
        subject_id="u_wang",
        context=VisibilityContext.private("u_wang"),
        transcript="用户: 别在群里说我的 Opus\n助手: 行，我不说",
    )
    repo.upsert_atom(
        MemoryAtom(
            id="pub1",
            namespace="team-1",
            subject_id="u_wang",
            kind=AtomKind.FACT,
            content="小王 Opus 用量很高",
            source_visibility=SourceVisibility.PUBLIC,
            sensitivity=Sensitivity.PUBLIC,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    result = mem.recall(
        namespace="team-1",
        subject_ids=["u_wang"],
        context=VisibilityContext.public(),
        query="谁 Opus 最多",
    )
    assert result.released_atoms == []
    assert result.deflection_reason.value == "commitment"
    session.close()
