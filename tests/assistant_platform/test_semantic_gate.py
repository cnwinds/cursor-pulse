from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from assistant_platform.memory.semantic.domain import (
    AtomKind,
    Commitment,
    CommitmentType,
    DeflectionReason,
    ReviewDecision,
    SemanticAtom,
    Sensitivity,
    SourceVisibility,
    VisibilityContext,
)
from assistant_platform.memory.semantic.gate import apply_disclosure_gate
from assistant_platform.memory.semantic.models import Base
from assistant_platform.memory.semantic.recall import recall_memories
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository


def _atom(
    atom_id: str,
    subject_id: str,
    content: str,
    *,
    sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
    confidence: float = 1.0,
) -> SemanticAtom:
    now = datetime.now(timezone.utc)
    return SemanticAtom(
        id=atom_id,
        namespace="team-1",
        subject_id=subject_id,
        kind=AtomKind.FACT,
        content=content,
        source_visibility=SourceVisibility.PRIVATE,
        sensitivity=sensitivity,
        confidence=confidence,
        created_at=now,
        last_seen_at=now,
    )


def test_public_context_blocks_confidential():
    atoms = [_atom("a1", "u_wang", "小王 Opus 用量很高", sensitivity=Sensitivity.CONFIDENTIAL)]
    released, blocked, reason = apply_disclosure_gate(
        atoms=atoms,
        context=VisibilityContext.public(),
        commitments=[],
    )
    assert released == []
    assert len(blocked) == 1
    assert reason == DeflectionReason.PRIVACY_DEFAULT


def test_private_self_can_see_confidential():
    atoms = [_atom("a1", "u_wang", "小王 Opus 用量很高", sensitivity=Sensitivity.CONFIDENTIAL)]
    released, blocked, reason = apply_disclosure_gate(
        atoms=atoms,
        context=VisibilityContext.private("u_wang"),
        commitments=[],
    )
    assert len(released) == 1
    assert blocked == []
    assert reason == DeflectionReason.NONE


def test_private_other_blocks_confidential():
    atoms = [_atom("a1", "u_wang", "小王 Opus 用量很高", sensitivity=Sensitivity.CONFIDENTIAL)]
    released, blocked, reason = apply_disclosure_gate(
        atoms=atoms,
        context=VisibilityContext.private("u_li"),
        commitments=[],
    )
    assert released == []
    assert len(blocked) == 1
    assert reason == DeflectionReason.PRIVACY_DEFAULT


def test_commitment_blocks_in_public_context():
    atoms = [_atom("a1", "u_wang", "小王 Opus 用量很高", sensitivity=Sensitivity.PUBLIC)]
    now = datetime.now(timezone.utc)
    commitments = [
        Commitment(
            id="c1",
            namespace="team-1",
            counterparty_id="u_wang",
            type=CommitmentType.PROMISED,
            statement="不在群里提小王的 Opus",
            scope={"topic_keywords": ["Opus", "小王"]},
            status="active",
            created_at=now,
        )
    ]
    released, blocked, reason = apply_disclosure_gate(
        atoms=atoms,
        context=VisibilityContext.public(),
        commitments=commitments,
    )
    assert released == []
    assert len(blocked) == 1
    assert reason == DeflectionReason.COMMITMENT


def test_reviewer_block_union_with_deterministic():
    atoms = [
        _atom("a1", "u_wang", "公开信息", sensitivity=Sensitivity.PUBLIC),
        _atom("a2", "u_wang", "机密信息", sensitivity=Sensitivity.CONFIDENTIAL),
    ]
    review = ReviewDecision(
        release_ids=["a1"],
        block_ids=[],
        deflection_reason=DeflectionReason.NONE,
    )
    released, blocked, _ = apply_disclosure_gate(
        atoms=atoms,
        context=VisibilityContext.public(),
        commitments=[],
        review=review,
    )
    assert {a.id for a in released} == {"a1"}
    assert {a.id for a in blocked} == {"a2"}


def test_low_confidence_never_released():
    atoms = [_atom("a1", "u_wang", "不确定", confidence=0.3)]
    released, blocked, reason = apply_disclosure_gate(
        atoms=atoms,
        context=VisibilityContext.private("u_wang"),
        commitments=[],
    )
    assert released == []
    assert len(blocked) == 1
    assert reason == DeflectionReason.PRIVACY_DEFAULT


@pytest.fixture
def memory_repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repo = SemanticMemoryRepository(session)
    yield repo, session
    session.close()


def test_recall_logs_disclosure(memory_repo):
    repo, session = memory_repo
    repo.upsert_atom(_atom("a1", "u_wang", "机密", sensitivity=Sensitivity.CONFIDENTIAL))
    result = recall_memories(
        repo,
        reviewer=None,
        namespace="team-1",
        subject_ids=["u_wang"],
        context=VisibilityContext.public(),
        query="谁 Opus 最多",
    )
    assert result.released_atoms == []
    assert result.deflection_reason == DeflectionReason.PRIVACY_DEFAULT
    assert result.disclosure_id is not None
    session.commit()
