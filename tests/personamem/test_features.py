from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from personamem.domain import AtomKind, MemoryAtom, Sensitivity, SourceVisibility
from personamem.domain import VisibilityContext
from personamem.engine import MemoryEngine
from personamem.models import Base
from personamem.principles_seed import DEFAULT_BOTTOM_LINE_RULES, seed_bottom_line_principles
from personamem.ports import SystemClock
from personamem.repository import SqlAlchemyMemoryRepository
from personamem.retrieval import HashingEmbedder, rank_atoms
from personamem.reflectors import RuleBasedReflector


def test_seed_bottom_line_principles():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = SqlAlchemyMemoryRepository(session)
    clock = SystemClock()

    seeded = seed_bottom_line_principles(repo, clock, "ns-1")
    assert len(seeded) == len(DEFAULT_BOTTOM_LINE_RULES)

    again = seed_bottom_line_principles(repo, clock, "ns-1")
    assert again == []
    session.close()


def test_semantic_ranking_prefers_relevant_atom():
    now = datetime.now(timezone.utc)
    atoms = [
        MemoryAtom(
            id="1",
            namespace="n",
            subject_id="u",
            kind=AtomKind.FACT,
            content="喜欢早上喝咖啡",
            source_visibility=SourceVisibility.PRIVATE,
            sensitivity=Sensitivity.CONFIDENTIAL,
            confidence=1.0,
            created_at=now,
            last_seen_at=now,
        ),
        MemoryAtom(
            id="2",
            namespace="n",
            subject_id="u",
            kind=AtomKind.FACT,
            content="本月 Opus 用量偏高",
            source_visibility=SourceVisibility.PRIVATE,
            sensitivity=Sensitivity.CONFIDENTIAL,
            confidence=1.0,
            created_at=now,
            last_seen_at=now,
        ),
    ]
    ranked = rank_atoms(atoms, "Opus 用多少", embedder=HashingEmbedder(), top_k=1)
    assert ranked[0].id == "2"


def test_evolution_adds_learned_principle():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = SqlAlchemyMemoryRepository(session)
    mem = MemoryEngine(repo=repo, reflector=RuleBasedReflector())

    for _ in range(3):
        repo.log_disclosure(
            namespace="team-1",
            context=VisibilityContext.public(),
            query_excerpt="谁 Opus 最多",
            released_atom_ids=[],
            blocked_atom_ids=["x"],
            deflection_reason="privacy_default",
        )

    added = mem.evolve("team-1")
    assert len(added.principles) >= 1
    assert all(p.tier.value == "learned" for p in added.principles)
    session.close()
