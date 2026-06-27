from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from personamem.domain import SourceVisibility, VisibilityContext
from personamem.engine import MemoryEngine
from personamem.models import Base
from personamem.repository import SqlAlchemyMemoryRepository
from personamem.responders import RuleBasedResponder
from pulse.memory_adapter.llm import RuleBasedDistiller, RuleBasedReviewer


@pytest.fixture
def mem_engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    repo = SqlAlchemyMemoryRepository(session)
    mem = MemoryEngine(
        repo=repo,
        distiller=RuleBasedDistiller(),
        reviewer=RuleBasedReviewer(),
        responder=RuleBasedResponder(),
        conversation_turn_limit=5,
    )
    yield mem, session
    session.close()


def test_conversation_turns_in_reply(mem_engine):
    mem, session = mem_engine
    mem.record_turn(
        namespace="team-1",
        subject_id="u1",
        role="user",
        content="我习惯月底再交",
        visibility=SourceVisibility.PRIVATE,
    )
    mem.record_turn(
        namespace="team-1",
        subject_id="u1",
        role="assistant",
        content="好的，记得 Export CSV 私聊我",
        visibility=SourceVisibility.PRIVATE,
    )
    session.commit()

    turns = mem._repo.list_recent_turns("team-1", "u1")
    assert len(turns) == 2
    assert turns[0].content == "我习惯月底再交"

    text = mem.reply(
        namespace="team-1",
        subject_ids=["u1"],
        context=VisibilityContext.private("u1"),
        user_message="好的",
        display_name="小王",
        is_group=False,
        subject_id="u1",
    )
    assert text


def test_evolution_action_executor(mem_engine):
    mem, session = mem_engine
    from personamem.domain import EvolutionActionProposal, EvolutionActionResult
    from personamem.evolution import ActionExecutor

    class FakeExecutor(ActionExecutor):
        def execute(self, *, namespace: str, action: EvolutionActionProposal) -> EvolutionActionResult:
            return EvolutionActionResult(action_type=action.action_type, status="executed", detail="ok")

    mem._executor = FakeExecutor()
    mem._reflector = __import__("personamem.reflectors", fromlist=["RuleBasedReflector"]).RuleBasedReflector()

    for _ in range(3):
        mem._repo.log_disclosure(
            namespace="team-1",
            context=VisibilityContext.public(),
            query_excerpt="test",
            released_atom_ids=[],
            blocked_atom_ids=["x"],
            deflection_reason="privacy_default",
        )

    result = mem.evolve("team-1")
    assert result.principles or result.actions
    session.close()
