from __future__ import annotations

from personamem.distill import distill_conversation
from personamem.domain import DisclosureResult, EvolutionResult, Principle, PrincipleTier, SourceVisibility
from personamem.embeddings import CachedAtomEmbedder, rank_atoms_with_cache
from personamem.evolution import run_evolution
from personamem.persona import Persona
from personamem.ports import ActionExecutor, Clock, Distiller, MemoryRepository, Responder, Reviewer, SystemClock
from personamem.principles_seed import seed_bottom_line_principles
from personamem.recall import recall_memories
from personamem.responders import RuleBasedResponder
from personamem.retrieval import HashingEmbedder, rank_atoms


class PrincipleManager:
    def __init__(self, repo: MemoryRepository, clock: Clock):
        self._repo = repo
        self._clock = clock

    def add(
        self,
        *,
        namespace: str,
        tier: str,
        rule: str,
        origin: str | None = None,
    ) -> Principle:
        import uuid

        principle = Principle(
            id=str(uuid.uuid4()),
            namespace=namespace,
            tier=PrincipleTier(tier),
            rule=rule,
            status="active",
            created_at=self._clock.now(),
            origin=origin,
        )
        return self._repo.add_principle(principle)

    def seed_defaults(self, namespace: str) -> list[Principle]:
        return seed_bottom_line_principles(self._repo, self._clock, namespace)


class MemoryEngine:
    def __init__(
        self,
        repo: MemoryRepository,
        distiller: Distiller | None = None,
        reviewer: Reviewer | None = None,
        responder: Responder | None = None,
        reflector=None,
        executor: ActionExecutor | None = None,
        clock: Clock | None = None,
        persona: Persona | None = None,
        embedder=None,
        retrieval_top_k: int = 8,
        conversation_turn_limit: int = 10,
        conversation_keep: int = 20,
        evolution_auto_execute: bool = True,
    ):
        self._repo = repo
        self._distiller = distiller
        self._reviewer = reviewer
        self._responder = responder or RuleBasedResponder()
        self._reflector = reflector
        self._executor = executor
        self._clock = clock or SystemClock()
        self._persona = persona or Persona()
        self._embedder = embedder or HashingEmbedder()
        self._retrieval_top_k = retrieval_top_k
        self._conversation_turn_limit = conversation_turn_limit
        self._conversation_keep = conversation_keep
        self._evolution_auto_execute = evolution_auto_execute
        self.principles = PrincipleManager(repo, self._clock)

    def ensure_seeded(self, namespace: str) -> list[Principle]:
        return self.principles.seed_defaults(namespace)

    def recall(
        self,
        *,
        namespace: str,
        subject_ids: list[str],
        context,
        query: str,
    ) -> DisclosureResult:
        return recall_memories(
            self._repo,
            self._reviewer,
            namespace=namespace,
            subject_ids=subject_ids,
            context=context,
            query=query,
        )

    def distill(
        self,
        *,
        namespace: str,
        subject_id: str,
        context,
        transcript: str,
    ) -> tuple[list, list]:
        if self._distiller is None:
            return [], []
        return distill_conversation(
            self._repo,
            self._distiller,
            self._clock,
            namespace=namespace,
            subject_id=subject_id,
            context=context,
            transcript=transcript,
        )

    def _rank_released(self, atoms: list, query: str) -> list:
        if isinstance(self._embedder, CachedAtomEmbedder):
            return rank_atoms_with_cache(
                atoms, query, self._embedder, top_k=self._retrieval_top_k
            )
        return rank_atoms(
            atoms, query, embedder=self._embedder, top_k=self._retrieval_top_k
        )

    def record_turn(
        self,
        *,
        namespace: str,
        subject_id: str,
        role: str,
        content: str,
        visibility: SourceVisibility,
    ) -> None:
        self._repo.append_turn(
            namespace=namespace,
            subject_id=subject_id,
            role=role,
            content=content,
            visibility=visibility,
            created_at=self._clock.now(),
        )
        self._repo.prune_turns(namespace, subject_id, keep=self._conversation_keep)

    def reply(
        self,
        *,
        namespace: str,
        subject_ids: list[str],
        context,
        user_message: str,
        display_name: str,
        is_group: bool,
        disclosure: DisclosureResult | None = None,
        subject_id: str | None = None,
    ) -> str:
        now = self._clock.now()
        disclosure = disclosure or self.recall(
            namespace=namespace,
            subject_ids=subject_ids,
            context=context,
            query=user_message,
        )
        ranked = self._rank_released(disclosure.released_atoms, user_message)

        recent_turns = []
        if subject_id:
            recent_turns = self._repo.list_recent_turns(
                namespace, subject_id, limit=self._conversation_turn_limit
            )

        schedule_note = self._persona.schedule_note(now)
        return self._responder.reply(
            persona=self._persona,
            user_message=user_message,
            display_name=display_name,
            disclosure=disclosure,
            ranked_atoms=ranked,
            is_group=is_group,
            recent_turns=recent_turns,
            schedule_note=schedule_note,
            now=now,
        )

    def evolve(
        self,
        namespace: str,
        *,
        min_confidence: float = 0.7,
    ) -> EvolutionResult:
        return run_evolution(
            self._repo,
            self._reflector,
            self._clock,
            namespace=namespace,
            min_confidence=min_confidence,
            executor=self._executor,
            auto_execute=self._evolution_auto_execute,
        )
